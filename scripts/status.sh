#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PID_FILE="$ROOT/runtime/storagegrid-usage-proxy.pid"

is_proxy_pid() {
    pid=$1
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    if [ -r "/proc/$pid/cmdline" ]; then
        tr '\000' ' ' < "/proc/$pid/cmdline" | grep -Fq 'storagegrid_usage_proxy.py'
        return $?
    fi
    return 0
}

if [ ! -f "$PID_FILE" ]; then
    echo "status=stopped"
    exit 1
fi

PID=$(cat "$PID_FILE" 2>/dev/null || true)
if is_proxy_pid "$PID"; then
    echo "status=running"
    echo "pid=$PID"
    exit 0
fi

echo "status=stale_pid_file"
rm -f "$PID_FILE"
exit 1