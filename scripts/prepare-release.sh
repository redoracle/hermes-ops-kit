#!/bin/bash
set -euo pipefail
VERSION="$1"
echo "Preparing release ${VERSION}"

# Update version in pyproject.toml
sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml
echo "Updated pyproject.toml to ${VERSION}"

echo "${VERSION}" > .release-prepared
