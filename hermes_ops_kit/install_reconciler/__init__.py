"""Runtime installation reconciler — detect-only (M1).

Reconciles four states that can drift apart after a source update:

* source repository state (pyproject.toml declarations)
* installed distribution metadata (dist-info / direct_url.json)
* generated executables (console scripts + wrappers)
* runtime state (what the target interpreter can actually load)

Guiding principle: **detect broadly, repair narrowly.**
"""

from .state import (
    ActualInstallation,
    ExpectedInstallation,
    Finding,
    HealthReport,
    HealthStatus,
    InstallationMode,
    RuntimeContext,
    Severity,
)
from .discovery import discover_actual_state
from .resolver import resolve_expected_state
from .evaluator import evaluate
from .fingerprint import installation_abi_fingerprint

__all__ = [
    "ActualInstallation",
    "ExpectedInstallation",
    "Finding",
    "HealthReport",
    "HealthStatus",
    "InstallationMode",
    "RuntimeContext",
    "Severity",
    "discover_actual_state",
    "resolve_expected_state",
    "evaluate",
    "installation_abi_fingerprint",
]
