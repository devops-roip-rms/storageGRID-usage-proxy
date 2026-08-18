#!/bin/sh
set -eu
MARKER="storagegrid-usage-proxy-autostart"
if ! command -v crontab >/dev/null 2>&1; then
    echo "ERROR: crontab is not available" >&2
    exit 1
fi
CURRENT=$(crontab -l 2>/dev/null || true)
printf '%s\n' "$CURRENT" | grep -v "$MARKER" | crontab - || true
echo "autostart_removed=yes"
