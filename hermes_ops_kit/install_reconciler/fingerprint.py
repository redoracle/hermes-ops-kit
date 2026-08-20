"""Installation ABI fingerprint.

Deterministic SHA-256 over ONLY the declarations that can require a
reinstall when they change:

* console scripts ([project.scripts])
* plugin entry-points ([project.entry-points])
* dependencies ([project.dependencies] / installed Requires-Dist)
* build-system backend
* declared package topology

Implementation-only changes (``*.py``, README, docs, tests) do NOT
invalidate the fingerprint. A same-version topology or entry-point
change MUST change it.
"""

from __future__ import annotations

import hashlib
import json

from .state import ActualInstallation, ExpectedInstallation


def _digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def expected_fingerprint(expected: ExpectedInstallation) -> str:
    """Fingerprint of what the source declares.

    Only mutually-observable fields: build backend and package topology
    are not recorded in dist-info, so they are excluded here and covered
    by dedicated findings (EDITABLE_TOPOLOGY_DRIFT) in the evaluator.
    """
    return _digest(
        {
            "scripts": sorted(expected.console_scripts.items()),
            "plugin_entry_points": sorted(expected.plugin_entry_points.items()),
            "dependencies": sorted(_normalize_req(d) for d in expected.dependencies),
        }
    )


def actual_fingerprint(actual: ActualInstallation) -> str:
    """Fingerprint of what the installed runtime exposes.

    Mirrors :func:`expected_fingerprint` exactly so the two hashes are
    comparable. Optional-dependency extras (``; extra == "dev"``) are
    excluded — they are not part of the base installation ABI.
    """
    return _digest(
        {
            "scripts": sorted((n, s.entry) for n, s in actual.console_scripts.items()),
            "plugin_entry_points": sorted(
                (n, p.entry) for n, p in actual.plugin_entry_points.items()
            ),
            "dependencies": sorted(
                _normalize_req(d) for d in actual.requires if not _is_extra(d)
            ),
        }
    )


def _is_extra(req: str) -> bool:
    return "extra ==" in req or "extra==" in req


def _normalize_req(req: str) -> str:
    """Strip environment markers and version specifiers, canonicalize name."""
    name = req.split(";")[0]
    for sep in ("===", "==", ">=", "<=", "~=", "!=", ">", "<"):
        name = name.split(sep)[0]
    return name.strip().lower().replace(".", "-").replace("_", "-")


def installation_abi_fingerprint(
    state: ExpectedInstallation | ActualInstallation,
) -> str:
    """Dispatcher kept for API symmetry; prefer the typed helpers above."""
    if isinstance(state, ExpectedInstallation):
        return expected_fingerprint(state)
    return actual_fingerprint(state)
