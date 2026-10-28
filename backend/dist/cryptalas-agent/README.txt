CryptALAS agent — USB install
=============================

One command installs the agent, starts it, and keeps it alive
(launchd on macOS, systemd on Linux, scheduled task on Windows).
It multicasts a beacon so your platform finds it automatically.

macOS / Linux
-------------
  ./install.sh                          # install + start (zero config)
  ./install.sh --name gpu-1 --device cuda
  ./install.sh --name gpu-1 --register http://PLATFORM:8787
  ./install.sh --uninstall

Windows (double-click or from cmd)
----------------------------------
  install.bat                           # install + start
  install.bat gpu-1 cuda
  install.bat gpu-1 cuda http://PLATFORM:8787

What it does
------------
- installs the agent binary to ~/.cryptalas (or %LOCALAPPDATA%\CryptALAS)
- registers it as a supervised service that restarts it if it ever dies
- the agent then multicasts its beacon (239.255.42.99:8788) every 30s

Verify it connected
-------------------
On the platform:  http://PLATFORM:8787  ->  "agents" tab
or                curl -s http://PLATFORM:8787/api/agents

Self-update + self-heal
-----------------------
The agent self-heals (restarts on hang) and self-updates from a
-update-manifest URL. For automatic updates, run it with:
  ./install.sh --name gpu-1 --device cuda
  # then the agent pulls new builds from its -update-manifest URL.

Files
-----
  cryptalas              macOS arm64
  cryptalas-linux-amd64  Linux x86_64
  cryptalas.exe          Windows x86_64
  gpu/cuda_train.py      CUDA trainer (optional)
  install.sh / install.bat
