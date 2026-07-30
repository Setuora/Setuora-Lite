#!/usr/bin/env bash
set -Eeuo pipefail

SELF_PATH="$(cd -- "$(dirname -- "$0")" && pwd -P)/$(basename -- "$0")"
INSTALL_PARENT="${XDG_DATA_HOME:-$HOME/.local/share}/setuora"
INSTALL_DIR="$INSTALL_PARENT/Setuora-Lite-linux"
PAYLOAD_MARKER="__SETUORA_PAYLOAD_BELOW__"

if [ -f "$INSTALL_DIR/.env" ]; then
  ACTION="update"
  echo "Existing Setuora Lite installation found."
  echo "Stopping the current application before updating..."
  "$INSTALL_DIR/setuora" stop
else
  ACTION="setup"
  echo "No existing Setuora Lite installation was found."
  echo "Starting a new installation..."
fi

PAYLOAD_LINE="$(awk -v marker="$PAYLOAD_MARKER" '$0 == marker { print NR + 1; exit }' "$SELF_PATH")"
if [ -z "$PAYLOAD_LINE" ]; then
  echo "The Setuora Lite installer payload is missing or damaged." >&2
  exit 1
fi

mkdir -p "$INSTALL_PARENT"
tail -n +"$PAYLOAD_LINE" "$SELF_PATH" | tar -xzf - -C "$INSTALL_PARENT"
chmod 700 "$INSTALL_DIR/setuora"

echo "Application files installed in: $INSTALL_DIR"
if [ "$ACTION" = "update" ]; then
  "$INSTALL_DIR/setuora" preflight
  exec "$INSTALL_DIR/setuora" update
fi

exec "$INSTALL_DIR/setuora" setup
exit 0
__SETUORA_PAYLOAD_BELOW__
