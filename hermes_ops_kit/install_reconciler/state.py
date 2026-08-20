"""Data contracts for the runtime installation reconciler.

All dataclasses are pure data: no side effects, JSON-serializable via
``to_dict()`` so the ``install doctor --json`` schema stays stable.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

SCHEMA_VERSION = 1


class InstallationMode(str, Enum):
    EDITABLE = "editable"
    REGULAR = "regular"
    UNKNOWN = "unknown"


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    REPAIRABLE = "REPAIRABLE"
    DIAGNOSE_ONLY = "DIAGNOSE_ONLY"
    UNSAFE = "UNSAFE"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class RuntimeContext:
    """Which runtime we are inspecting — never assume ``sys.executable``."""

    python_executable: str
    environment_prefix: str = ""
    base_prefix: str = ""
    python_version: str = ""
    plugin_source: str = ""
    installation_mode: InstallationMode = InstallationMode.UNKNOWN
    runtime_role: str = "dev"
    installer_policy: str = "auto"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["installation_mode"] = self.installation_mode.value
        return d


@dataclass
class ConsoleScript:
    """One console script as declared AND as materialized on disk."""

    name: str
    entry: str = ""  # "module:attr" from distribution metadata
    script_path: str | None = None  # generated executable, if found
    shebang: str = ""  # supplementary evidence only
    load_ok: bool | None = None  # runtime probe result
    load_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PluginEntryPoint:
    """One ``hermes_agent.plugins`` entry-point for this distribution."""

    name: str
    entry: str = ""
    load_ok: bool | None = None
    load_error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActualInstallation:
    """Facts discovered in the target runtime — observations only."""

    distribution_present: bool = False
    distribution_name: str = ""
    version: str = ""
    dist_info_path: str = ""
    direct_url: str = ""
    is_editable: bool = False
    origin_url: str = ""
    console_scripts: dict[str, ConsoleScript] = field(default_factory=dict)
    plugin_entry_points: dict[str, PluginEntryPoint] = field(default_factory=dict)
    requires: list[str] = field(default_factory=list)  # installed Requires-Dist
    extra_distributions: list[str] = field(default_factory=list)  # duplicates found
    probe_error: str = ""
    fingerprint: str = ""

    def to_dict(self) -> dict:
        return {
            "distribution_present": self.distribution_present,
            "distribution_name": self.distribution_name,
            "version": self.version,
            "dist_info_path": self.dist_info_path,
            "direct_url": self.direct_url,
            "is_editable": self.is_editable,
            "origin_url": self.origin_url,
            "console_scripts": {
                k: v.to_dict() for k, v in self.console_scripts.items()
            },
            "plugin_entry_points": {
                k: v.to_dict() for k, v in self.plugin_entry_points.items()
            },
            "requires": self.requires,
            "extra_distributions": self.extra_distributions,
            "probe_error": self.probe_error,
            "fingerprint": self.fingerprint,
        }


@dataclass
class ExpectedInstallation:
    """What the current source declares (pyproject.toml)."""

    package_name: str = ""
    version: str = ""
    console_scripts: dict[str, str] = field(
        default_factory=dict
    )  # name -> "module:attr"
    plugin_entry_points: dict[str, str] = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    build_backend: str = ""
    packages: list[str] = field(default_factory=list)
    pyproject_path: str = ""
    fingerprint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Finding:
    """One non-fatal observation. Findings are not mutually exclusive."""

    code: str
    severity: Severity
    observed: str = ""
    expected: str = ""
    evidence: str = ""
    repairable: bool = False

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "observed": self.observed,
            "expected": self.expected,
            "evidence": self.evidence,
            "repairable": self.repairable,
        }


# Finding codes (stable API — used by tests and the JSON schema)
MISSING_DISTRIBUTION = "MISSING_DISTRIBUTION"
DISTRIBUTION_METADATA_DRIFT = "DISTRIBUTION_METADATA_DRIFT"
CONSOLE_ENTRYPOINT_DRIFT = "CONSOLE_ENTRYPOINT_DRIFT"
PLUGIN_ENTRYPOINT_DRIFT = "PLUGIN_ENTRYPOINT_DRIFT"
GENERATED_EXECUTABLE_DRIFT = "GENERATED_EXECUTABLE_DRIFT"
EDITABLE_TOPOLOGY_DRIFT = "EDITABLE_TOPOLOGY_DRIFT"
SOURCE_ORIGIN_ALLOWED = "SOURCE_ORIGIN_ALLOWED"
SOURCE_ORIGIN_DISALLOWED = "SOURCE_ORIGIN_DISALLOWED"
INTERPRETER_MISMATCH = "INTERPRETER_MISMATCH"
INTERPRETER_AMBIGUOUS = "INTERPRETER_AMBIGUOUS"
MULTIPLE_INSTALLATIONS = "MULTIPLE_INSTALLATIONS"
RUNTIME_PROBE_FAILURE = "RUNTIME_PROBE_FAILURE"
DEPENDENCY_DECLARATION_DRIFT = "DEPENDENCY_DECLARATION_DRIFT"
PACKAGING_METADATA_DRIFT = "PACKAGING_METADATA_DRIFT"


@dataclass
class HealthReport:
    """Pure evaluation result — no side effects, no repair hints executed."""

    overall: HealthStatus = HealthStatus.HEALTHY
    runtime: RuntimeContext = field(default_factory=RuntimeContext)
    actual: ActualInstallation = field(default_factory=ActualInstallation)
    expected: ExpectedInstallation = field(default_factory=ExpectedInstallation)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "overall": self.overall.value,
            "runtime": self.runtime.to_dict(),
            "actual": self.actual.to_dict(),
            "expected": self.expected.to_dict(),
            "findings": [f.to_dict() for f in self.findings],
        }
