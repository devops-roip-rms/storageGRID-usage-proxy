#!/usr/bin/env bash
set -euo pipefail

: "${VERSION:?VERSION is required}"
: "${IMAGE_NAME:?IMAGE_NAME is required}"
: "${GITHUB_SHA:?GITHUB_SHA is required}"
: "${GITHUB_SERVER_URL:?GITHUB_SERVER_URL is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"

echo "Building Docker image:"
echo "  Image:   $IMAGE_NAME"
echo "  Version: $VERSION"
echo "  Commit:  $GITHUB_SHA"

docker build \
  --build-arg APP_VERSION="$VERSION" \
  --label org.opencontainers.image.revision="$GITHUB_SHA" \
  --label org.opencontainers.image.source="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY" \
  -t "$IMAGE_NAME:$VERSION" \
  .