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
    echo "StorageGRID Usage Proxy is not running (no PID file)"
    exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || true)
if ! is_proxy_pid "$PID"; then
    echo "StorageGRID Usage Proxy is not running (stale PID file removed)"
    rm -f "$PID_FILE"
    exit 0
fi

kill "$PID"
i=0
while is_proxy_pid "$PID"; do
    i=$((i + 1))
    if [ "$i" -ge 20 ]; then
        echo "Process $PID did not stop within 20 seconds" >&2
        exit 1
    fi
    sleep 1
done
rm -f "$PID_FILE"
echo "StorageGRID Usage Proxy stopped"
