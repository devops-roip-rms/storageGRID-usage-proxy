#!/bin/sh
set -eu

if [ ! -f TAG ]; then
    echo "ERROR: TAG file does not exist"
    exit 1
fi

VERSION="$(tr -d '[:space:]' < TAG)"

if [ -z "$VERSION" ]; then
    echo "ERROR: TAG file is empty"
    exit 1
fi

if ! printf '%s' "$VERSION" | grep -Eq '^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$'; then
    echo "ERROR: Invalid Docker image tag: $VERSION"
    exit 1
fi

if [ -z "${IMAGE_NAME:-}" ]; then
    echo "ERROR: IMAGE_NAME is not defined"
    exit 1
fi

BASE_IMAGE="${BASE_IMAGE:-cr.io:5000/python:3.11-slim-bookworm}"
TAR_FILE="storagegrid-usage-proxy_${VERSION}.tar"

echo "Building Docker image:"
echo "  Image:      ${IMAGE_NAME}:${VERSION}"
echo "  Base image: $BASE_IMAGE"
echo "  TAR file:   $TAR_FILE"

docker build \
    --build-arg BASE_IMAGE="$BASE_IMAGE" \
    --build-arg APP_VERSION="$VERSION" \
    --label org.opencontainers.image.revision="$CI_COMMIT_SHA" \
    --label org.opencontainers.image.source="$CI_PROJECT_URL" \
    -t "${IMAGE_NAME}:${VERSION}" \
    .

echo "Saving Docker image to $TAR_FILE"

docker save \
    -o "$TAR_FILE" \
    "${IMAGE_NAME}:${VERSION}"

echo "Generating SHA256 checksum"

sha256sum "$TAR_FILE" > "${TAR_FILE}.sha256"

# compose.yml reads IMAGE_TAG from this file.
printf 'IMAGE_TAG=%s\n' "$VERSION" > .env

printf '%s\n' "$VERSION" > IMAGE_VERSION.txt

echo "Docker offline package created successfully:"
echo "  $TAR_FILE"
echo "  ${TAR_FILE}.sha256"
echo "  IMAGE_VERSION.txt"
echo "  .env"