#!/usr/bin/env bash
# =============================================================================
# hermes-lite-chat
# =============================================================================
# Strona   : automation-ai.eu - Automatyzacja AI
# Autor    : Zbigniew Czechowski
# WWW      : https://automation-ai.eu/
# E-mail   : kontakt@automation-ai.eu
# Licencja : MIT
# =============================================================================
#
# Installs hermes-lite-chat as a systemd --user service, so it starts
# automatically at boot and keeps running after logout (see the README
# section "Autostart on boot" / "Autostart przy starcie systemu").
#
# Safe to re-run: it just regenerates the unit file and re-applies it.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_NAME="hermes-lite-chat"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/${SERVICE_NAME}.service"
VENV_PYTHON="$REPO_DIR/.venv/bin/python"

if [[ ! -x "$VENV_PYTHON" ]]; then
    echo "error: $VENV_PYTHON not found." >&2
    echo "Run the Setup steps in README.md first:" >&2
    echo "  python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" == "yes" ]]; then
    echo "==> Lingering already enabled for user '$USER'"
else
    echo "==> Enabling lingering for user '$USER' (lets the service survive logout / start at boot)"
    if ! loginctl enable-linger "$USER"; then
        echo "warning: could not enable linger automatically (try: sudo loginctl enable-linger $USER)." >&2
        echo "Without it, the service still runs now but stops when you log out / on reboot." >&2
    fi
fi

mkdir -p "$UNIT_DIR"

cat > "$UNIT_FILE" <<EOF
[Unit]
Description=Hermes Lite Chat - Web terminal for Hermes Agent
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
ExecStart="$VENV_PYTHON" -m app.main
WorkingDirectory=$REPO_DIR
Environment="PATH=$REPO_DIR/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$HOME/.local/bin"
Restart=always
RestartSec=5
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
EOF

echo "==> Wrote $UNIT_FILE"

systemctl --user daemon-reload
systemctl --user enable --now "${SERVICE_NAME}.service"

cat <<EOF
==> Done. hermes-lite-chat is now running and will start automatically at boot.

Manage it with:
  systemctl --user status  ${SERVICE_NAME}
  systemctl --user stop    ${SERVICE_NAME}
  systemctl --user start   ${SERVICE_NAME}
  systemctl --user restart ${SERVICE_NAME}
  journalctl --user -u ${SERVICE_NAME} -f
EOF

