#!/bin/bash
set -euo pipefail
VERSION="$1"
echo "Preparing release ${VERSION}"

# Update version 4-way (invariant enforced by tests/test_versions.py):
# pyproject.toml == plugin.yaml == hermes_ops_kit.__version__
#                 == plugin_scanner.__version__
sed -i "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml
sed -i "s/^version: \".*\"/version: \"${VERSION}\"/" plugin.yaml
sed -i "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" hermes_ops_kit/__init__.py
sed -i "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" hermes_ops_kit/security/plugin_scanner/__init__.py
echo "Updated pyproject.toml, plugin.yaml, hermes_ops_kit/__init__.py, plugin_scanner/__init__.py to ${VERSION}"

echo "${VERSION}" > .release-prepared
