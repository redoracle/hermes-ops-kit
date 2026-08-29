"""Version 4-way invariant (plan amendment F).

pyproject.toml == plugin.yaml == hermes_ops_kit.__version__
== hermes_ops_kit.security.plugin_scanner.__version__

``SCANNER_VERSION`` in cache.py is a *cache-schema* version and is
deliberately independent — it invalidates cached scan results.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import hermes_ops_kit  # noqa: E402
from hermes_ops_kit.security.plugin_scanner import (  # noqa: E402
    __version__ as scanner_version,
)


def _pyproject_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def _plugin_yaml_version() -> str:
    for line in (REPO_ROOT / "plugin.yaml").read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            return stripped.split(":", 1)[1].strip().strip("\"'")
    raise AssertionError("plugin.yaml has no version field")


def _compat_version() -> str:
    import re as _re

    from hermes_ops_kit import ops_config_io

    text = open(
        str(Path(ops_config_io.__file__).parent / "config" / "compat.yaml")
    ).read()
    m = _re.search(r'^ops_kit_version:\s*"?([0-9][^"\s]*)', text, _re.M)
    return m.group(1) if m else "missing"


def test_version_four_way_invariant():
    versions = {
        "pyproject.toml": _pyproject_version(),
        "plugin.yaml": _plugin_yaml_version(),
        "hermes_ops_kit.__version__": hermes_ops_kit.__version__,
        "plugin_scanner.__version__": scanner_version,
        "config/compat.yaml ops_kit_version": _compat_version(),
    }
    assert len(set(versions.values())) == 1, f"version drift: {versions}"
