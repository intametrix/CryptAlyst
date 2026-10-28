#!/bin/bash
# CryptALAS agent — one-command installer (macOS / Linux). No Go needed.
#   ./install.sh                          # install + start (zero config)
#   ./install.sh --name gpu-1 --device cuda
#   ./install.sh --name gpu-1 --register http://platform:8787
#   ./install.sh --uninstall
set -u
cd "$(dirname "$0")" || exit 1

NAME=""; DEVICE="auto"; REGISTER=""; PREFIX="$HOME/.cryptalas"; UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --name) NAME="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --register) REGISTER="$2"; shift 2 ;;
    --prefix) PREFIX="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --help|-h) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown: $1" >&2; exit 2 ;;
  esac
done

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS/$ARCH" in
  Darwin/arm64|Darwin/aarch64) SRC=cryptalas ;;
  Linux/x86_64|Linux/amd64)   SRC=cryptalas-linux-amd64 ;;
  *) echo "unsupported platform: $OS $ARCH" >&2; exit 2 ;;
esac

LABEL="com.cryptalyst.agent"
BIN="$PREFIX/cryptalas"
LOG="$PREFIX/agent.log"

if [ "$UNINSTALL" = "1" ]; then
  case "$OS" in
    Darwin) launchctl unload "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null; rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" ;;
    Linux)  systemctl --user stop "$LABEL" 2>/dev/null; systemctl --user disable "$LABEL" 2>/dev/null; rm -f "$HOME/.config/systemd/user/$LABEL.service"; systemctl --user daemon-reload 2>/dev/null ;;
  esac
  rm -f "$BIN"; echo "removed"; exit 0
fi

[ -f "$SRC" ] || { echo "missing $SRC — run from the install folder" >&2; exit 1; }

echo "installing -> $PREFIX ($OS $ARCH)"
mkdir -p "$PREFIX"
cp "$SRC" "$BIN"
chmod +x "$BIN"
[ -d gpu ] && cp -R gpu "$PREFIX/gpu"

case "$OS" in
  Darwin)
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$HOME/Library/LaunchAgents/$LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$BIN</string>
    <string>-device</string><string>$DEVICE</string>
    $([ -n "$NAME" ] && echo "<string>-name</string><string>$NAME</string>")
    $([ -n "$REGISTER" ] && echo "<string>-register</string><string>$REGISTER</string>")
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
PLIST
    launchctl unload "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null
    launchctl load "$HOME/Library/LaunchAgents/$LABEL.plist" ;;
  Linux)
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/$LABEL.service" <<UNIT
[Unit]
Description=CryptALAS agent
After=network.target
[Service]
ExecStart=$BIN -device $DEVICE$([ -n "$NAME" ] && echo " -name $NAME")$([ -n "$REGISTER" ] && echo " -register $REGISTER")
Restart=always
RestartSec=2
[Install]
WantedBy=default.target
UNIT
    systemctl --user daemon-reload
    systemctl --user enable --now "$LABEL" ;;
esac

echo "done — agent is installed, running, and self-supervised."
echo "  status:  tail -f $LOG"
echo "  name:    ${NAME:-[hostname]}   device: $DEVICE"
echo "  uninstall: $0 --uninstall"
