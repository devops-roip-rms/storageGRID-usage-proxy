#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$ROOT/storagegrid_usage_proxy.py" --env-file "$ROOT/config/proxy.env" --check-config