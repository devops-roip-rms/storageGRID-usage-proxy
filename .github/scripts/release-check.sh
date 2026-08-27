#!/usr/bin/env bash
set -euo pipefail

: "${EVENT_NAME:?EVENT_NAME is required}"
: "${DEFAULT_BRANCH:?DEFAULT_BRANCH is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

SHOULD_PACKAGE=false

if [[ "$EVENT_NAME" == "workflow_dispatch" &&
      "$GITHUB_REF_TYPE" == "branch" &&
      "$GITHUB_REF_NAME" == "$DEFAULT_BRANCH" ]]; then

  SHOULD_PACKAGE=true

elif [[ "$EVENT_NAME" == "push" &&
        "$GITHUB_REF_TYPE" == "branch" &&
        "$GITHUB_REF_NAME" == "$DEFAULT_BRANCH" ]]; then

  if [[ -z "${BEFORE_SHA:-}" ||
        "$BEFORE_SHA" == "0000000000000000000000000000000000000000" ]]; then

    if git diff-tree \
      --no-commit-id \
      --name-only \
      -r "$GITHUB_SHA" |
      grep -Fxq "TAG"; then

      SHOULD_PACKAGE=true
    fi

  elif git diff --name-only "$BEFORE_SHA" "$GITHUB_SHA" |
    grep -Fxq "TAG"; then

    SHOULD_PACKAGE=true
  fi
fi

echo "should_package=$SHOULD_PACKAGE" >> "$GITHUB_OUTPUT"
echo "Package image: $SHOULD_PACKAGE"