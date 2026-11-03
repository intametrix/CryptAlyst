<#
.SYNOPSIS
  Installer for the CryptALAS compute agent on a Windows box.

.DESCRIPTION
  Installs the agent as a scheduled task that starts at boot, with the flags it
  needs to find the platform. Downloads the binary, verifies its checksum against
  the manifest, waits for /health, and prints the result. Re-running it upgrades
  in place. -Uninstall removes the task and the install directory.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-agent.ps1 -Platform http://192.168.1.142:8787 -Marketdata http://192.168.1.142:8796 -UpdateServer http://192.168.1.142:8899
#>
[CmdletBinding()]
param(
  [string]$Platform     = "http://192.168.1.142:8787",
  [string]$Marketdata   = "http://192.168.1.142:8796",
  [string]$UpdateServer = "http://192.168.1.142:8899",
  [string]$InstallDir   = "$env:ProgramData\CryptALAS",
  [int]$Port            = 8810,
  [switch]$Uninstall,
  [switch]$SkipTask,
  [string]$Name         = "dev"
)

$ErrorActionPreference = "Stop"
$taskName = "CryptALAS Agent"

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "    $msg" -ForegroundColor Yellow }

if ($Uninstall) {
  Write-Step "Removing scheduled task '$taskName'"
  schtasks /End /TN "$taskName" 2>$null | Out-Null
  schtasks /Delete /TN "$taskName" /F 2>$null | Out-Null
  Write-Step "Stopping any running agent"
  Get-Process cryptalas -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  if (Test-Path $InstallDir) {
    Write-Step "Removing $InstallDir"
    Remove-Item -Recurse -Force $InstallDir -ErrorAction SilentlyContinue
  }
  Write-Ok "uninstalled"
  exit 0
}

# ---------------------------------------------------------------- privileges --
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Write-Warn2 "not elevated: the task will run only while this user is logged on"
}

# ------------------------------------------------------------------ download --
Write-Step "Installing to $InstallDir"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$exe = Join-Path $InstallDir "cryptalas.exe"

$manifestUrl = "$UpdateServer/manifest-cryptalas.exe.json"
$binaryUrl   = "$UpdateServer/cryptalas.exe"
$expectedSha = $null
try {
  $manifest = Invoke-RestMethod -Uri $manifestUrl -TimeoutSec 15
  if ($manifest.binary) { $binaryUrl = $manifest.binary }
  $expectedSha = $manifest.sha256
  Write-Ok "manifest: version $($manifest.version)"
} catch {
  Write-Warn2 "manifest unavailable ($UpdateServer) — downloading the binary directly"
}

Write-Step "Downloading agent"
$tmp = Join-Path $env:TEMP ("cryptalas-" + [guid]::NewGuid().ToString("N") + ".exe")
$ProgressPreference = "SilentlyContinue"
Invoke-WebRequest -Uri $binaryUrl -OutFile $tmp -UseBasicParsing -TimeoutSec 180
$size = (Get-Item $tmp).Length
Write-Ok ("downloaded {0:N1} MB" -f ($size / 1MB))

if ($expectedSha) {
  $actual = (Get-FileHash -Path $tmp -Algorithm SHA256).Hash.ToLower()
  if ($actual -ne $expectedSha.ToLower()) {
    Remove-Item -Force $tmp -ErrorAction SilentlyContinue
    throw "checksum mismatch: expected $expectedSha, got $actual"
  }
  Write-Ok "sha256 verified"
} else {
  Write-Warn2 "no checksum in the manifest — skipped verification"
}

# An agent already running holds the file open on Windows, so stop it first.
Get-Process cryptalas -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 500
Move-Item -Force $tmp $exe
Write-Ok "installed $exe"

# --------------------------------------------------------------------- task --
# Flags as the agent defines them: -register is the platform BASE url, and the
# self-update manifest is passed by its full URL.
$args = @(
  "--addr", "0.0.0.0:$Port",
  "--marketdata", $Marketdata,
  "--register", $Platform,
  "--update-manifest", "$UpdateServer/manifest-cryptalas.exe.json",
  # Concurrent jobs: the optimiser dispatches several sweeps at once, and the
  # default (a quarter of the CPU count) throttles the GPU to four.
  "--max-jobs", "12",
  "--name", $Name
) -join " "

if (-not $SkipTask) {
  Write-Step "Registering scheduled task '$taskName' (starts at boot)"
  $action  = "`"$exe`" $args"
  # schtasks keeps this compatible with older Windows Server builds, and the
  # task survives reboots without a service wrapper.
  $create = "schtasks /Create /TN `"$taskName`" /TR `"$action`" /SC ONSTART /RU SYSTEM /RL HIGHEST /F"
  $out = cmd /c $create 2>&1
  if ($LASTEXITCODE -ne 0) {
    Write-Warn2 "ONSTART task failed, falling back to at-logon"
    $create = "schtasks /Create /TN `"$taskName`" /TR `"$action`" /SC ONLOGON /RL HIGHEST /F"
    $out = cmd /c $create 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Warn2 ($out | Out-String) }
  } else {
    Write-Ok "scheduled task created"
  }
}

# ------------------------------------------------------------------- start ---
Write-Step "Starting the agent"
Start-Process -FilePath $exe -ArgumentList $args -WindowStyle Hidden
Start-Sleep -Seconds 2

Write-Step "Waiting for /health on port $Port"
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
  try {
    $h = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
    $healthy = $true
    break
  } catch { Start-Sleep -Seconds 1 }
}

if ($healthy) {
  Write-Ok "agent is up: http://127.0.0.1:$Port"
  try {
    $gpu = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/gpu" -TimeoutSec 5
    Write-Ok "device=$($gpu.device) gpu_present=$($gpu.gpu_present) version=$($gpu.git_sha)"
    if (-not $gpu.gpu_present) {
      Write-Warn2 "no CUDA device reported — forecasts will run on CPU"
    }
  } catch {
    Write-Warn2 "agent answered but /gpu did not: $($_.Exception.Message)"
  }
  Write-Host ""
  Write-Host "  Dashboard: $Platform" -ForegroundColor Green
  Write-Host "  The agent will appear under 'agents' within 30 seconds." -ForegroundColor Green
  exit 0
}

Write-Warn2 "agent did not answer on port $Port — check $InstallDir and the task's last run result"
exit 1
