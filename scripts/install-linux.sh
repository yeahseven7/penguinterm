#!/usr/bin/env bash
# PenguinTerm installer for Linux (Arch, Ubuntu, Debian, Fedora, Mint, ...)
#
# Usage:
#   ./scripts/install-linux.sh              # install + enable user service
#   ./scripts/install-linux.sh --no-service # install only, don't start anything
#   ./scripts/install-linux.sh --uninstall  # remove app, service and venv
#
# The install is fully per-user by default (no root). It creates:
#   ~/.local/share/penguinterm/app     — application files
#   ~/.local/share/penguinterm/venv    — Python virtual environment
#   ~/.config/systemd/user/penguinterm.service — user systemd unit
#   ~/.local/share/penguinterm/config.json     — generated configuration
#   ~/.local/share/penguinterm/uninstall.sh    — uninstaller
set -euo pipefail

PREFIX="${PENGUINTERM_PREFIX:-$HOME/.local/share/penguinterm}"
APP_DIR="$PREFIX/app"
VENV_DIR="$PREFIX/venv"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_FILE="$SERVICE_DIR/penguinterm.service"
PORT="${PENGUINTERM_PORT:-8443}"
DO_SERVICE=1
DO_UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --no-service) DO_SERVICE=0 ;;
    --uninstall) DO_UNINSTALL=1 ;;
    --port=*) PORT="${arg#*=}" ;;
    -h|--help)
      grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;32m[penguinterm]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[penguinterm]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[penguinterm]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- uninstall
if [ "$DO_UNINSTALL" -eq 1 ]; then
  log "Uninstalling PenguinTerm from $PREFIX"
  if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now penguinterm.service 2>/dev/null || true
  fi
  rm -f "$SERVICE_FILE"
  systemctl --user daemon-reload 2>/dev/null || true
  rm -rf "$PREFIX"
  log "Done. Your configuration database (penguinterm.db) was inside $PREFIX and is now removed."
  log "Shell config was not touched. Uninstall complete."
  exit 0
fi

# ------------------------------------------------------------- preconditions
command -v python3 >/dev/null 2>&1 || die "python3 is required (install it with your package manager)"
PY_OK=$(python3 -c 'import sys; print(1 if sys.version_info >= (3,12) else 0)')
[ "$PY_OK" = "1" ] || die "Python 3.12+ is required (found: $(python3 --version 2>&1))"

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "Source:  $SRC_DIR"
log "Prefix:  $PREFIX"

# Vendored frontend dependencies aren't in git; fetch them (pinned + verified) if missing.
if [ ! -f "$SRC_DIR/frontend/assets/vendor/xterm.js" ]; then
  log "Fetching vendored frontend dependencies…"
  bash "$SRC_DIR/scripts/fetch-vendor.sh"
fi

# ------------------------------------------------------------------ install
mkdir -p "$APP_DIR" "$SERVICE_DIR"
log "Copying application files…"
cp -a "$SRC_DIR/backend" "$SRC_DIR/frontend" "$SRC_DIR/config" "$SRC_DIR/requirements.txt" "$SRC_DIR/README.md" "$APP_DIR/"

log "Creating virtual environment…"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
log "Installing dependencies…"
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

# generate configuration if missing (based on the shipped example, with the chosen port)
if [ ! -f "$PREFIX/config.json" ]; then
  log "Generating configuration ($PREFIX/config.json)…"
  "$VENV_DIR/bin/python" - "$PREFIX/config.json" "$APP_DIR/config/penguinterm.example.json" "$PORT" <<'PY'
import json, sys
dest, example, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
with open(example) as fh:
    cfg = json.load(fh)
cfg["port"] = port
with open(dest, "w") as fh:
    json.dump(cfg, fh, indent=2)
PY
else
  log "Keeping existing configuration $PREFIX/config.json"
fi

# tell the app where its data lives
DATA_HINT="export PENGUINTERM_DATA_DIR='$PREFIX'"

# ------------------------------------------------------------- uninstaller
cat > "$PREFIX/uninstall.sh" <<EOF
#!/usr/bin/env bash
# PenguinTerm uninstaller (generated). Preserves nothing; review before running.
exec "$SRC_DIR/scripts/install-linux.sh" --uninstall
EOF
chmod +x "$PREFIX/uninstall.sh"

# ------------------------------------------------------------------ service
if [ "$DO_SERVICE" -eq 1 ] && command -v systemctl >/dev/null 2>&1; then
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=PenguinTerm — decentralized multiplayer terminal
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=PENGUINTERM_DATA_DIR=$PREFIX
ExecStart=$VENV_DIR/bin/python -m backend.main
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  if systemctl --user enable --now penguinterm.service 2>/dev/null; then
    log "systemd user service enabled and started"
    log "Follow logs with: journalctl --user -u penguinterm -f"
  else
    warn "Could not enable the user service (is systemd running?)."
    warn "Data dir is set via: $DATA_HINT"
    warn "Start manually with: PENGUINTERM_DATA_DIR='$PREFIX' $VENV_DIR/bin/python -m backend.main"
  fi
  # lingering keeps the user service alive without an active login session
  if command -v loginctl >/dev/null 2>&1 && [ "$(id -u)" != "0" ]; then
    loginctl enable-linger "$USER" 2>/dev/null || true
  fi
else
  log "Service skipped. Start manually with:"
  log "  PENGUINTERM_DATA_DIR='$PREFIX' \"$VENV_DIR/bin/python\" -m backend.main"
fi

# ------------------------------------------------------------- next steps
cat <<EOF

$(log 'PenguinTerm installed.')
  Web UI : https://<this-machine>:$PORT  (accept the self-signed certificate once,
           or replace $PREFIX/tls/cert.pem + key.pem with your own)
  Config : $PREFIX/config.json
  Logs   : journalctl --user -u penguinterm -f   (or use the in-app diagnostics page)

Firewall (open inbound TCP $PORT, e.g. ufw):
  sudo ufw allow $PORT/tcp

Friends connect with: https://<your-host-or-dynamic-dns>:$PORT
No port forwarding? Use Tailscale (recommended) or ZeroTier — then give friends
your 100.x.y.z (Tailscale) or 192.168.192.x address; see README.
Uninstall: $PREFIX/uninstall.sh
EOF
