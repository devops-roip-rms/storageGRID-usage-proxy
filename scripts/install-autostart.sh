#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
MARKER="storagegrid-usage-proxy-autostart"

if ! command -v crontab >/dev/null 2>&1; then
    echo "ERROR: crontab is not available on this server." >&2
    echo "The proxy itself is ready, but reboot autostart cannot be installed with this method." >&2
    exit 1
fi

case "$ROOT" in
    *%*)
        echo "ERROR: project path contains %, which is unsafe in a crontab command" >&2
        exit 1
        ;;
esac

LINE="@reboot \"$ROOT/scripts/start.sh\" >>\"$ROOT/logs/autostart.log\" 2>&1 # $MARKER"
CURRENT=$(crontab -l 2>/dev/null || true)
{
    printf '%s\n' "$CURRENT" | grep -v "$MARKER" || true
    printf '%s\n' "$LINE"
} | crontab -

echo "autostart_installed=yes"
echo "method=user_crontab_at_reboot"
echo "project=$ROOT"