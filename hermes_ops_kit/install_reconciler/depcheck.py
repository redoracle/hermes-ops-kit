"""Resolved-dependency health: are the requirements actually satisfied?

The declaration check (``DEPENDENCY_DECLARATION_DRIFT``) compares metadata with
``pyproject.toml``. This module answers the other question, the one ``pip check``
asks: for the requirements the installed distribution declares, is a compatible
distribution really installed in the *target* environment?

* Collection (``COLLECT_SOURCE``) is stdlib-only source text. The same text runs
  inside the target interpreter (the probe) and in-process (the fast check), so
  the two can never disagree about what "installed" means.
* Evaluation (``check_dependencies``) runs in the reconciler's own process with
  ``packaging``, against the TARGET's marker environment, never the host's.
* Read-only: no installer, network or subprocess is involved here.
"""

from __future__ import annotations

from dataclasses import dataclass

COLLECT_SOURCE = r"""
def collect_environment():
    import os, platform, re, sys
    from importlib.metadata import distributions

    installed = {}
    for dist in distributions():
        name = dist.metadata["Name"]
        if name:
            # first on sys.path wins, as it does for imports
            installed.setdefault(re.sub(r"[-_.]+", "-", name).lower(), dist.version)

    info = sys.implementation.version
    implementation_version = "{0.major}.{0.minor}.{0.micro}".format(info)
    if info.releaselevel != "final":
        implementation_version += info.releaselevel[0] + str(info.serial)
    markers = {
        "implementation_name": sys.implementation.name,
        "implementation_version": implementation_version,
        "os_name": os.name,
        "platform_machine": platform.machine(),
        "platform_release": platform.release(),
        "platform_system": platform.system(),
        "platform_version": platform.version(),
        "python_full_version": platform.python_version(),
        "platform_python_implementation": platform.python_implementation(),
        "python_version": ".".join(platform.python_version_tuple()[:2]),
        "sys_platform": sys.platform,
    }
    return {"installed": installed, "markers": markers}
"""

_namespace: dict = {}
# nosemgrep: python.lang.security.audit.exec-detected.exec-detected
# (COLLECT_SOURCE is a constant in this file, the same text the target-interpreter probe runs)
exec(COLLECT_SOURCE, _namespace)  # noqa: S102
collect_environment = _namespace["collect_environment"]


_MARKER_KEYS = (
    "implementation_name",
    "implementation_version",
    "os_name",
    "platform_machine",
    "platform_release",
    "platform_system",
    "platform_version",
    "python_full_version",
    "platform_python_implementation",
    "python_version",
    "sys_platform",
)


@dataclass(frozen=True)
class DependencyProblem:
    kind: str  # "missing" | "incompatible"
    requirement: str
    installed: str | None = None


def check_dependencies(
    requires: list[str], installed: dict[str, str], markers: dict[str, str]
) -> tuple[list[DependencyProblem], str | None]:
    """Unmet requirements, and a reason when the check could not run.

    Requirements whose environment marker is false for the target (including
    every ``extra`` requirement, since no extra is selected) are not required.
    A missing ``packaging`` yields no problems and an explanation: unknown is
    never reported as healthy, and never crashes the reconciler.
    """
    try:
        from packaging.requirements import InvalidRequirement, Requirement
        from packaging.utils import canonicalize_name
    except ImportError as exc:
        return [], f"the 'packaging' library is not available ({exc})"

    if not isinstance(markers, dict) or not all(
        isinstance(markers.get(key), str) for key in _MARKER_KEYS
    ):
        # packaging would silently fill gaps from the HOST: refuse instead
        return [], "the target's marker environment is incomplete"
    installed = {
        name: version
        for name, version in installed.items()
        if isinstance(name, str) and isinstance(version, str)
    }
    environment = {**markers, "extra": ""}
    problems: list[DependencyProblem] = []
    for text in requires:
        if not isinstance(text, str):
            continue
        try:
            requirement = Requirement(text)
        except InvalidRequirement:
            continue  # not a requirement we can reason about
        if requirement.marker is not None and not requirement.marker.evaluate(
            environment
        ):
            continue
        version = installed.get(canonicalize_name(requirement.name))
        if version is None:
            problems.append(DependencyProblem("missing", str(requirement)))
        elif requirement.specifier and not requirement.specifier.contains(
            version, prereleases=True
        ):
            problems.append(
                DependencyProblem("incompatible", str(requirement), version)
            )
    return problems, None
