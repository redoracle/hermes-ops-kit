"""Hermes Ops Kit — Shared credential-read guard for local file reads.

Mirrors Hermes core ``agent/file_safety.py:raise_if_read_blocked`` (#57698): a
single chokepoint that media/vision/image-gen adapters call BEFORE ``open()`` on
a model/user-supplied local file path, refusing reads that target credential
stores (``.env``*, ``auth.json``, …) or prompt-injection carriers (Hermes
``skills/`` / ``.hub`` cache).

Integration, not re-implementation: when ops-kit runs inside the Hermes process
(the normal case for image adapters loaded by the ops-kit-router provider), the
guard delegates to core's ``agent.file_safety.raise_if_read_blocked`` so the
read boundary stays perfectly aligned with core. A self-contained denylist is
kept as a fallback for standalone/test contexts where ``agent.file_safety`` is
not importable.

The guard is ONLY for model/user-supplied paths. System credential loading
(e.g. ``load_dotenv`` reading ``~/.hermes/.env``) must NOT route through this
guard — those are trusted system reads, not model-supplied input, and gating
them would break credential loading (exactly as core's own env loader is not
gated).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# ── Best-effort delegation to Hermes core ──────────────────────────────
# Resolved at import time so a runtime `export HERMES_REDACT_SECRETS=false`-style
# mutation cannot swap the guard mid-session. Missing core → fall back to the
# local denylist below.
_core_raise: Optional[callable] = None  # type: ignore[type-arg]
_core_error: Optional[callable] = None  # type: ignore[type-arg]
try:  # pragma: no cover - depends on running inside the Hermes process
    from agent.file_safety import (  # type: ignore[import-not-found]
        get_read_block_error as _core_error,
        raise_if_read_blocked as _core_raise,
    )
except Exception:
    _core_raise = None
    _core_error = None

# ── Fallback denylist (mirrors core agent/file_safety.py) ──────────────

# Project-local env/secret files anywhere on disk.
_BLOCKED_ENV_BASENAMES = frozenset(
    {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        ".env.test",
        ".env.staging",
        ".envrc",
    }
)

# Credential-store basenames under HERMES_HOME / global Hermes root.
_BLOCKED_HERMES_BASENAMES = frozenset(
    {
        "auth.json",
        "auth.lock",
        ".anthropic_oauth.json",
        "webhook_subscriptions.json",
        "bws_cache.json",
    }
)

# HERMES_HOME subtrees that are prompt-injection carriers or credential stores.
# NOTE: skills/.hub is handled explicitly below — core blocks ONLY the injection
# cache (skills/.hub), not user skill files (agent/file_safety.py:257-260).
_BLOCKED_HERMES_DIRS = frozenset({".hub", "auth", "mcp-tokens"})


def _local_block_error(path: str) -> Optional[str]:
    """Local denylist implementation — used when core's guard is unavailable."""
    if not path:
        return None
    try:
        resolved = Path(os.path.expanduser(path)).resolve()
    except Exception:
        return None
    name = resolved.name

    # (a) project .env* anywhere on disk
    if name in _BLOCKED_ENV_BASENAMES:
        return f"read blocked: {name!r} is a credential store (env file)"

    # (b)(c) credential stores / injection carriers under HERMES_HOME or the
    # Hermes root (~/.hermes). In profile mode HERMES_HOME points at
    # ~/.hermes/profiles/<name>, so root-level credential stores (auth.json,
    # .anthropic_oauth.json, …) must be checked against BOTH roots — mirrors
    # core agent/file_safety._hermes_home_path + _hermes_root_path.
    roots: list[Path] = []
    for env_root in (os.environ.get("HERMES_HOME", "~/.hermes"), "~/.hermes"):
        try:
            real = Path(os.path.expanduser(env_root)).resolve()
            if real not in roots:
                roots.append(real)
        except Exception:
            continue
    for home in roots:
        try:
            rel = resolved.relative_to(home)
            top = rel.parts[0] if rel.parts else ""
            rel_str = "/".join(rel.parts)
            # skills/.hub is the prompt-injection cache; core blocks ONLY this,
            # not user skill files (file_safety.py:257-260).
            if rel_str == "skills/.hub" or rel_str.startswith("skills/.hub/"):
                return f"read blocked: {path!r} is under Hermes skills/.hub (prompt-injection cache)"
            if top in _BLOCKED_HERMES_DIRS:
                return f"read blocked: {path!r} is under Hermes {top}/ (credential/injection store)"
            if name in _BLOCKED_HERMES_BASENAMES:
                return f"read blocked: {name!r} is a Hermes credential store"
        except ValueError:
            continue  # path not under this root
        except Exception:
            return None  # never break loading on a guard-internal fault
    return None


def get_read_block_error(path: str) -> Optional[str]:
    """Return an error message if *path* is a denied read, else ``None``.

    Delegates to core's ``agent.file_safety.get_read_block_error`` when
    available; otherwise applies the local denylist.
    """
    if _core_error is not None:
        try:
            return _core_error(path)
        except Exception:
            pass  # guard-internal fault → fall through to local check
    return _local_block_error(path)


def raise_if_read_blocked(path: str) -> None:
    """Raise ``ValueError`` if *path* is a denied credential-store/skills read.

    Call this BEFORE ``open()`` on any model/user-supplied local file path
    (e.g. an image-gen ``image_path`` / ``reference_image_urls`` entry). Mirrors
    Hermes core ``agent.file_safety.raise_if_read_blocked``. Never raises on
    guard-internal errors — local-file loading must not break because the guard
    itself threw.
    """
    if _core_raise is not None:
        try:
            _core_raise(path)
            return  # allowed — core returned without raising
        except ValueError:
            raise  # blocked — propagate
        except Exception:
            pass  # guard-internal fault → fall back to local check
    err = _local_block_error(path)
    if err:
        raise ValueError(err)
