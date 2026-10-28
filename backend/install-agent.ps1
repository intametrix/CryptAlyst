# install-agent.ps1 — install the CryptALAS agent on Windows as a supervised,
# auto-restarting scheduled task, with a name you define.
#
#   .\install-agent.ps1 -Name gpu-1 -Device cuda
#   .\install-agent.ps1 -Name gpu-1 -Device cuda -Register http://platform:8787
#   .\install-agent.ps1 -Name gpu-1 -InstallPath C:\cryptalas
#   .\install-agent.ps1 -Uninstall
#
# Requirements: cryptalas.exe in the same folder (build it with
#   ./build-agent.sh windows   from the backend dir, or copy it here).
#
# The agent multicasts its beacon on 239.255.42.99:8788, so a platform on the
# same site discovers it automatically; -Register is only needed cross-site.
param(
  [string]$Name = "",
  [string]$Device = "auto",
  [string]$Register = "",
  [string]$InstallPath = "$env:LOCALAPPDATA\CryptALAS",
  [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "CryptALAS Agent"
$Exe = Join-Path $InstallPath "cryptalas.exe"
$Bat = Join-Path $InstallPath "supervise-cryptalas.bat"
$Log = Join-Path $InstallPath "agent.log"

function Remove-Agent {
  Write-Host "uninstall: removing the agent service"
  try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (Test-Path $InstallPath) { Remove-Item -Recurse -Force $InstallPath -ErrorAction SilentlyContinue }
  Write-Host "  removed $InstallPath and the scheduled task"
  exit 0
}

if ($Uninstall) { Remove-Agent }

if (-not (Test-Path "cryptalas.exe")) {
  Write-Error "cryptalas.exe not found in $(Get-Location). Build it with: ./build-agent.sh windows"
  exit 1
}

Write-Host "install: installing to $InstallPath"
New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null
Copy-Item "cryptalas.exe" $Exe -Force

# The agent args, baked into the supervisor loop.
$agentArgs = @("-device", $Device)
if ($Name)       { $agentArgs += "-name", $Name }
if ($Register)   { $agentArgs += "-register", $Register }
$argLine = ($agentArgs -join " ")

# Supervisor loop: swap in a self-update (.exe.new), then run forever. The
# agent's own self-heal watchdog exits on a hang; this loop restarts it.
$bat = @"
@echo off
REM CryptALAS agent supervisor — never dies, swaps in self-updates.
:loop
if exist "$Exe.new" (
  echo supervisor: swapping in cryptalas.exe.new
  move /Y "$Exe.new" "$Exe" >nul
)
"$Exe" $argLine
echo supervisor: cryptalas exited (%%ERRORLEVEL%%), restarting in 2s
timeout /t 2 /nobreak >nul
goto loop
"@
Set-Content -Path $Bat -Value $bat -Encoding ASCII

# Register a scheduled task that runs the supervisor at every logon.
Write-Host "install: registering scheduled task '$TaskName' (at logon)"
$action  = New-ScheduledTaskAction -Execute $Bat -WorkingDirectory $InstallPath
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Seconds 5) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited | Out-Null

# Start it now so the agent is live immediately.
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "install complete"
Write-Host "  agent:   $Exe"
Write-Host "  name:    $($(if ($Name) { $Name } else { '[hostname]' }))"
Write-Host "  device:  $Device"
Write-Host "  status:  $Log  (look for 'beacon sent — …')"
Write-Host "  platform sees it at: http://<platform>:8787/api/agents"
Write-Host "  uninstall: .\install-agent.ps1 -Uninstall"
