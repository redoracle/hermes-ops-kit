"""Entry-point acceptance — semantic assertions (plan amendment B).

Asserts the *declared* plugin entry point semantics from pyproject.toml
and that the referenced module exposes a top-level callable ``register``.
String-based assertions (``':register' in repr``) are evasion-prone and
deliberately not used.

Provenance isolation (site-packages vs source tree) is exercised by the
fresh-venv acceptance lanes (wheel/sdist/editable) which run outside the
pytest session where ``sys.path`` cannot be controlled.
"""

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

sys.path.insert(0, str(REPO_ROOT))


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_entry_point_declared_semantically() -> None:
    """hermes_agent.plugins group maps hermes-ops-kit → module-only target."""
    eps = _pyproject()["project"]["entry-points"]["hermes_agent.plugins"]
    assert eps == {"hermes-ops-kit": "hermes_ops_kit"}, eps


def test_entry_point_module_semantics() -> None:
    """ep.load() target: module with top-level callable register."""
    mod = importlib.import_module("hermes_ops_kit")
    register = getattr(mod, "register", None)
    assert callable(register), "hermes_ops_kit.register must be callable"
    assert mod.__name__ == "hermes_ops_kit"


def test_console_scripts_target_package() -> None:
    """Every console script must dispatch into the hermes_ops_kit package."""
    scripts = _pyproject()["project"]["scripts"]
    assert scripts, "console scripts must be declared"
    for name, target in scripts.items():
        module, _, attr = target.partition(":")
        assert module.startswith("hermes_ops_kit."), f"{name}: {target}"
        assert attr, f"{name}: missing attr in {target}"
