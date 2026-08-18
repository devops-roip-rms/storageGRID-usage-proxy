#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PID_FILE="$ROOT/runtime/storagegrid-usage-proxy.pid"
LOG_FILE="$ROOT/logs/storagegrid-usage-proxy.log"

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

mkdir -p "$ROOT/runtime" "$ROOT/logs"

if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" 2>/dev/null || true)
    if is_proxy_pid "$PID"; then
        echo "StorageGRID Usage Proxy is already running (PID $PID)"
        exit 0
    fi
    rm -f "$PID_FILE"
fi

"$ROOT/scripts/check-config.sh"
nohup "$ROOT/scripts/run.sh" >>"$LOG_FILE" 2>&1 &
PID=$!
printf '%s\n' "$PID" > "$PID_FILE"
sleep 1

if is_proxy_pid "$PID"; then
    echo "StorageGRID Usage Proxy started (PID $PID)"
    echo "Log: $LOG_FILE"
else
    echo "StorageGRID Usage Proxy failed to start. Last log lines:" >&2
    tail -n 30 "$LOG_FILE" >&2 || true
    rm -f "$PID_FILE"
    exit 1
fi
