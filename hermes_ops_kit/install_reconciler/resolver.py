"""Resolve the expected installation state from the source repository."""

from __future__ import annotations

import tomllib
from pathlib import Path

from .fingerprint import expected_fingerprint
from .state import ExpectedInstallation

PLUGIN_GROUP = "hermes_agent.plugins"


def resolve_expected_state(
    pyproject_path: Path | str, package_name: str = "hermes-ops-kit"
) -> ExpectedInstallation:
    """Read ``pyproject.toml`` and build the declared (expected) state.

    Missing/unparseable pyproject yields an empty ExpectedInstallation —
    the evaluator turns that into a finding rather than crashing.
    """
    path = Path(pyproject_path)
    expected = ExpectedInstallation(package_name=package_name, pyproject_path=str(path))
    if not path.is_file():
        return expected

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (tomllib.TOMLDecodeError, OSError):
        return expected

    project = data.get("project", {})
    expected.version = str(project.get("version", ""))
    expected.console_scripts = {
        str(k): str(v) for k, v in project.get("scripts", {}).items()
    }
    expected.plugin_entry_points = {
        str(k): str(v)
        for k, v in project.get("entry-points", {}).get(PLUGIN_GROUP, {}).items()
    }
    expected.dependencies = [str(d) for d in project.get("dependencies", [])]
    expected.build_backend = str(data.get("build-system", {}).get("build-backend", ""))

    # Package discovery: hatchling [tool.hatch.build.targets.wheel].packages,
    # setuptools [tool.setuptools].packages / packages.find, or fallback to
    # the distribution name normalized as a top-level package.
    tool = data.get("tool", {})
    hatch_pkgs = (
        tool.get("hatch", {}).get("build", {}).get("targets", {}).get("wheel", {})
    ).get("packages")
    if hatch_pkgs:
        expected.packages = [str(p) for p in hatch_pkgs]
    else:
        setuptools = tool.get("setuptools", {})
        if "packages" in setuptools:
            expected.packages = [str(p) for p in setuptools["packages"]]
        elif "packages-find" in setuptools or "packages" in setuptools:
            find = setuptools.get("packages-find") or setuptools.get("packages.find")
            if isinstance(find, dict) and find.get("include"):
                expected.packages = [str(p).split(".*")[0] for p in find["include"]]
    if not expected.packages:
        normalized = package_name.replace("-", "_")
        src = path.parent / "src" / normalized
        expected.packages = [f"src/{normalized}" if src.is_dir() else normalized]

    expected.fingerprint = expected_fingerprint(expected)
    return expected
