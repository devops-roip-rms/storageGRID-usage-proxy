#!/bin/sh
set -eu

BASE_IMAGE="${BASE_IMAGE:-cr.io:5000/python:3.11-slim-bookworm}"

for attempt in 1 2 3 4 5; do
    echo "Docker pull attempt $attempt/5: $BASE_IMAGE"

    if docker pull "$BASE_IMAGE"; then
        exit 0
    fi

    if [ "$attempt" -eq 5 ]; then
        echo "ERROR: Unable to pull $BASE_IMAGE after 5 attempts"
        exit 1
    fi

    sleep $((attempt * 15))
done