#!/bin/bash
set -euo pipefail
VERSION="$1"
echo "Preparing release ${VERSION}"

# Update version 5-way (invariant enforced by tests/test_versions.py):
# pyproject.toml == plugin.yaml == hermes_ops_kit.__version__
#                 == plugin_scanner.__version__ == config/compat.yaml ops_kit_version
# sed -i without a suffix argument is a hard error on macOS/BSD sed —
# detect GNU vs BSD and use the right in-place form.
if sed --version >/dev/null 2>&1; then
  SED_I=(sed -i)
else
  SED_I=(sed -i '')
fi
"${SED_I[@]}" "s/^version = \".*\"/version = \"${VERSION}\"/" pyproject.toml
"${SED_I[@]}" "s/^version: \".*\"/version: \"${VERSION}\"/" plugin.yaml
"${SED_I[@]}" "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" hermes_ops_kit/__init__.py
"${SED_I[@]}" "s/^__version__ = \".*\"/__version__ = \"${VERSION}\"/" hermes_ops_kit/security/plugin_scanner/__init__.py
"${SED_I[@]}" "s/^ops_kit_version: \".*\"/ops_kit_version: \"${VERSION}\"/" hermes_ops_kit/config/compat.yaml
echo "Updated pyproject.toml, plugin.yaml, hermes_ops_kit/__init__.py, plugin_scanner/__init__.py, config/compat.yaml to ${VERSION}"

echo "${VERSION}" > .release-prepared
