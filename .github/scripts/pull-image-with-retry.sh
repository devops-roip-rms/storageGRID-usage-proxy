#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:?Docker image is required}"
MAX_ATTEMPTS="${2:-5}"

for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  echo "Docker pull attempt $attempt/$MAX_ATTEMPTS: $IMAGE"

  if docker pull "$IMAGE"; then
    exit 0
  fi

  if [[ "$attempt" -eq "$MAX_ATTEMPTS" ]]; then
    echo "ERROR: Unable to pull $IMAGE after $MAX_ATTEMPTS attempts"
    exit 1
  fi

  sleep $((attempt * 15))
done