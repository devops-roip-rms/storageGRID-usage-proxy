#!/usr/bin/env bash
set -euo pipefail

: "${IMAGE_NAME:?IMAGE_NAME is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

if [[ ! -f TAG ]]; then
  echo "ERROR: TAG file does not exist"
  exit 1
fi

VERSION="$(tr -d '[:space:]' < TAG)"

if [[ -z "$VERSION" ]]; then
  echo "ERROR: TAG file is empty"
  exit 1
fi

if [[ ! "$VERSION" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "ERROR: Invalid Docker image tag: $VERSION"
  exit 1
fi

TAR_FILE="${IMAGE_NAME}_${VERSION}.tar"

echo "version=$VERSION" >> "$GITHUB_OUTPUT"
echo "tar_file=$TAR_FILE" >> "$GITHUB_OUTPUT"

printf '%s\n' "$VERSION" > IMAGE_VERSION.txt

echo "Image version: $VERSION"
echo "Archive file: $TAR_FILE"