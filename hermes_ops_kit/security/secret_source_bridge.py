"""Hermes Ops Kit — SecretSource bridge (read-side integration with Hermes core).

Hermes core owns the canonical secret-READ path: at startup,
``agent.secret_sources.registry.apply_all()`` fetches from all enabled sources
(Bitwarden, 1Password, …) and writes them to ``os.environ``, recording
provenance in ``hermes_cli.env_loader._SECRET_SOURCES`` (env_var → source label).
The SecretSource ABC is read-only and bulk (``fetch(cfg, home_path)``) — there
is no per-secret ``get(ref)`` API, so ops-kit cannot delegate a single read.

ops-kit's env rendering (``env/render_env.py``) reads from its own Vaultwarden
backend to produce ``.env.generated``. To avoid a split-brain where ops-kit and
core disagree on a variable, this bridge lets render_env query core's
provenance — which variables core already provides and from which source — so
ops-kit can annotate (``# source=``) and surface conflicts
(``also-provided-by=core:…``) instead of silently duplicating core's read path.

Integration, not re-implementation: ops-kit does NOT host its own
1Password/multi-vault reader. It queries core's provenance when core is
importable (the in-Hermes case) and degrades to ``{}`` when running standalone
(e.g. in tests), where render_env falls back to Vaultwarden-only. WRITES
(rotation: set/backup/restore) stay on the Vaultwarden backend — core's
SecretSource has no write API.
"""

from __future__ import annotations


def core_secret_sources() -> dict[str, str]:
    """Return ``{env_var: source_label}`` for vars core's SecretSource provides.

    Best-effort: returns ``{}`` when ``hermes_cli`` is not importable or
    ``apply_all`` has not run yet. Never raises.
    """
    try:
        from hermes_cli import env_loader  # type: ignore[import-not-found]
    except Exception:
        return {}
    try:
        sources = getattr(env_loader, "_SECRET_SOURCES", None)
        if isinstance(sources, dict):
            return {str(k): str(v) for k, v in sources.items()}
    except Exception:
        pass
    return {}
