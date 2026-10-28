#!/bin/bash
# install-agent.sh — build the CryptALAS agent and install it as a supervised
# background service (launchd on macOS, systemd on Linux) so it runs at login
# and restarts itself if it ever dies.
#
#   ./install-agent.sh                          # build + install + start
#   ./install-agent.sh --name gpu-1 --device cuda --register http://platform:8787
#   ./install-agent.sh --prefix ~/bin           # install somewhere else
#   ./install-agent.sh --uninstall              # stop + remove the service
#
# The agent multicasts its beacon on 239.255.42.99:8788, so a platform on the
# same site discovers it automatically; -register is only needed cross-site.
set -u
cd "$(dirname "$0")" || exit 1

PREFIX=""
NAME=""
DEVICE="auto"
REGISTER=""
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --name) NAME="$2"; shift 2 ;;
    --device) DEVICE="$2"; shift 2 ;;
    --register) REGISTER="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --help|-h) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
done

OS="$(uname -s)"
case "$OS" in
  Darwin) [ -z "$PREFIX" ] && PREFIX="$HOME/.cryptalas" ;;
  Linux)  [ -z "$PREFIX" ] && PREFIX="$HOME/.cryptalas" ;;
  *) echo "unsupported OS: $OS (install-agent.sh is for macOS/Linux)" >&2; exit 2 ;;
esac

BIN="$PREFIX/cryptalas"
LABEL="com.cryptalyst.agent"
LOG="$PREFIX/agent.log"

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }

uninstall() {
  say "uninstall: removing the agent service"
  case "$OS" in
    Darwin)
      launchctl unload "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null
      rm -f "$HOME/Library/LaunchAgents/$LABEL.plist" ;;
    Linux)
      systemctl --user stop "$LABEL" 2>/dev/null
      systemctl --user disable "$LABEL" 2>/dev/null
      rm -f "$HOME/.config/systemd/user/$LABEL.service"
      systemctl --user daemon-reload 2>/dev/null ;;
  esac
  rm -f "$BIN"
  ok "removed $BIN and the service"
  exit 0
}

[ "$UNINSTALL" = "1" ] && uninstall

say "install: building the agent"
./build-agent.sh current >/dev/null || { bad "build failed"; exit 1; }

say "install: installing to $PREFIX"
mkdir -p "$PREFIX"
cp bin/cryptalas "$BIN"
chmod +x "$BIN"
ok "$BIN"

# The flags the service will run with.
ARGS=(-device "$DEVICE")
[ -n "$NAME" ] && ARGS+=(-name "$NAME")
[ -n "$REGISTER" ] && ARGS+=(-register "$REGISTER")

case "$OS" in
  Darwin)
    say "install: registering launchd service ($LABEL)"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$HOME/Library/LaunchAgents/$LABEL.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$BIN</string>
    <string>-device</string><string>$DEVICE</string>
    $([ -n "$NAME" ] && printf '<string>-name</string>\n    <string>%s</string>' "$NAME")
    $([ -n "$REGISTER" ] && printf '<string>-register</string>\n    <string>%s</string>' "$REGISTER")
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict></plist>
EOF
    launchctl unload "$HOME/Library/LaunchAgents/$LABEL.plist" 2>/dev/null
    launchctl load "$HOME/Library/LaunchAgents/$LABEL.plist"
    ok "loaded; keep-alive keeps it alive and restarts it on boot" ;;

  Linux)
    say "install: registering systemd user service ($LABEL)"
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/$LABEL.service" <<EOF
[Unit]
Description=CryptALAS agent
After=network.target

[Service]
ExecStart=$BIN -device $DEVICE$([ -n "$NAME" ] && printf ' -name %s' "$NAME")$([ -n "$REGISTER" ] && printf ' -register %s' "$REGISTER")
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$LABEL"
    ok "enabled; Restart=always keeps it alive and restarts it on login" ;;
esac

say "install complete"
echo
echo "  status:  see $LOG for 'beacon sent — …' lines"
echo "  platform sees it at:  http://<platform>:8787/api/agents   (agents tab on :8787)"
echo "  uninstall:  $0 --uninstall"
