"""Hermes Ops Kit — Headroom desired-state reconciliation.

The only writer that aligns ``~/.hermes/config.yaml`` with the desired
state in ``~/.hermes/ops-kit/headroom.yaml``.

Robustness contract ("Hermes never dies"):
  * ``fallback_providers`` are never written — they stay direct, so
    Hermes core degrades on its own when the proxy is unreachable.
  * The proxied route is applied only after a live health check; when
    the proxy cannot become healthy the route stays (or reverts to)
    direct and the result carries a warning instead of an error.
  * Every config.yaml write is preceded by a timestamped backup, and
    the pre-enable route is snapshotted for exact restore.

Overlay mechanism (verified against the Hermes runtime):
  ``model.provider: headroom`` + a named ``providers.headroom`` entry
  ({base_url, key_env}) resolved by ``_get_named_custom_runtime`` as a
  custom OpenAI-compatible provider.  Headroom forwards the client's
  Authorization header to the upstream, so ``key_env`` reuses the
  *upstream provider's* key env (e.g. NVIDIA_API_KEY) — no extra key.
"""

from __future__ import annotations

import copy
import json
import os
from typing import Any


from ..headroom_ops import daemon  # noqa: E402
from ..headroom_ops.settings import load_settings  # noqa: E402
from ..ops_config_io import HERMES_CONFIG, backup_file, load_yaml, save_yaml  # noqa: E402

PROVIDER_NAME = "headroom"


# ─── Route inspection helpers (shared with doctor/status) ─────────────


def is_proxied(hermes_cfg: dict, settings: dict) -> bool:
    """True when the primary route currently goes through Headroom."""
    model = hermes_cfg.get("model") or {}
    if str(model.get("provider", "")).strip().lower() == PROVIDER_NAME:
        return True
    base = str(model.get("base_url", "") or "").rstrip("/")
    return bool(base) and base == str(settings["base_url"]).rstrip("/")


def upstream_provider_entry(
    hermes_cfg: dict, settings: dict
) -> tuple[str, dict[str, Any]]:
    """(provider_name, providers entry) of the real upstream provider.

    When already proxied the previous primary is read from the snapshot;
    otherwise it is the current ``model.provider``.
    """
    model = hermes_cfg.get("model") or {}
    provider = str(model.get("provider", "")).strip()
    if provider.lower() == PROVIDER_NAME:
        snap = load_snapshot(settings)
        provider = str(((snap.get("model") or {}).get("provider")) or "").strip()
    entry = (hermes_cfg.get("providers") or {}).get(provider)
    return provider, entry if isinstance(entry, dict) else {}


def resolve_primary_provider(hermes_cfg: dict) -> tuple[str, bool]:
    """(effective primary provider, via_headroom) for observability tools.

    When the primary is the managed Headroom overlay, reporting tools
    must keep describing the *real* route (``nvidia via headroom``), so
    the proxied provider name is resolved back to the snapshotted
    upstream.  Falls back to the raw value on any error.
    """
    model = hermes_cfg.get("model") or {}
    provider = str(model.get("provider", "")).strip()
    if provider.lower() != PROVIDER_NAME:
        return provider, False
    try:
        settings = load_settings()
        upstream, _ = upstream_provider_entry(hermes_cfg, settings)
    except Exception:
        return provider, True
    return (upstream or provider), True


def collision_findings(hermes_cfg: dict, settings: dict) -> list[str]:
    """Fallback/provider entries that illegally point at the proxy.

    The graceful-degradation guarantee dies the moment a fallback (or a
    provider a fallback resolves through) targets the proxy itself.
    """
    marker = f"127.0.0.1:{int(settings.get('port', 8790))}"
    findings: list[str] = []
    for i, fb in enumerate(hermes_cfg.get("fallback_providers") or []):
        if isinstance(fb, dict) and marker in str(fb.get("base_url", "") or ""):
            findings.append(
                f"fallback_providers[{i}] ({fb.get('provider', '?')}) "
                f"points at the headroom proxy"
            )
    for name, entry in (hermes_cfg.get("providers") or {}).items():
        if name == PROVIDER_NAME or not isinstance(entry, dict):
            continue
        if marker in str(entry.get("base_url", "") or ""):
            findings.append(f"providers.{name} points at the headroom proxy")
    return findings


# ─── Snapshot ──────────────────────────────────────────────────────────


def load_snapshot(settings: dict) -> dict:
    try:
        with open(settings["state_file"]) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _write_snapshot(settings: dict, snapshot: dict) -> None:
    from ..env.atomic_write import atomic_write_json

    path = settings["state_file"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, snapshot)


def _clear_snapshot(settings: dict) -> None:
    try:
        os.remove(settings["state_file"])
    except OSError:
        pass


# ─── Reconciliation ────────────────────────────────────────────────────


def reconcile(dry_run: bool = False, desired_override: bool | None = None) -> dict:
    """Align config.yaml with the desired state. Never raises.

    ``desired_override`` lets ``enable/disable --dry-run`` preview the
    would-be action without persisting the desired state first.

    Returns ``{ok, desired, action, proxied_before, proxied_after,
    upstream, warnings, errors, backup, dry_run}``.  ``ok`` is False
    only on invariant violations (collisions) or unreadable config —
    a proxy that will not start is a *warning* plus a direct route.
    """
    result: dict[str, Any] = {
        "ok": True,
        "desired": None,
        "action": "noop",
        "proxied_before": None,
        "proxied_after": None,
        "upstream": None,
        "warnings": [],
        "errors": [],
        "backup": None,
        "dry_run": dry_run,
    }
    try:
        settings = load_settings()
        if desired_override is not None:
            settings["enabled"] = desired_override
        result["desired"] = "enabled" if settings.get("enabled") else "disabled"

        hermes_cfg = load_yaml(HERMES_CONFIG)
        if not hermes_cfg:
            result["ok"] = False
            result["errors"].append(f"cannot read {HERMES_CONFIG}")
            return result

        collisions = collision_findings(hermes_cfg, settings)
        if collisions:
            result["ok"] = False
            result["errors"].extend(collisions)
            result["errors"].append(
                "refusing to reconcile: fix the entries above — fallbacks "
                "must stay direct or graceful degradation is lost"
            )
            return result

        proxied = is_proxied(hermes_cfg, settings)
        result["proxied_before"] = proxied
        result["proxied_after"] = proxied

        if settings.get("enabled"):
            _reconcile_enable(settings, hermes_cfg, result, dry_run)
        else:
            _reconcile_disable(settings, hermes_cfg, result, dry_run)
    except Exception as exc:  # reconcile must never take the boot down
        result["ok"] = False
        result["errors"].append(f"unexpected reconcile error: {exc}")
    return result


def _restore_direct(
    settings: dict, hermes_cfg: dict, result: dict, dry_run: bool
) -> None:
    """Restore the snapshotted direct route (best-effort, never raises)."""
    snap = load_snapshot(settings)
    snap_model = snap.get("model") or {}
    if not snap_model.get("provider"):
        # Proxied without a snapshot (manual edit): derive a sane direct
        # route from the first fallback so Hermes keeps working.
        fallbacks = hermes_cfg.get("fallback_providers") or []
        first = fallbacks[0] if fallbacks and isinstance(fallbacks[0], dict) else {}
        if not first.get("provider"):
            result["warnings"].append(
                "proxied route found but no snapshot and no fallbacks — "
                "left unchanged; fix model.provider manually"
            )
            return
        snap_model = {
            "provider": first["provider"],
            "default": first.get("model", ""),
            "base_url": "",
        }
        result["warnings"].append(
            f"no snapshot — restoring primary from first fallback ({first['provider']})"
        )
    if dry_run:
        result["action"] = "would_restore_direct"
        result["proxied_after"] = False
        return

    model = hermes_cfg.setdefault("model", {})
    model.update(snap_model)
    providers = hermes_cfg.get("providers") or {}
    if snap.get("had_headroom_provider") and snap.get("prev_headroom_entry"):
        providers[PROVIDER_NAME] = snap["prev_headroom_entry"]
    else:
        providers.pop(PROVIDER_NAME, None)
    for aux_key, prev in (snap.get("aux") or {}).items():
        slot = (hermes_cfg.get("auxiliary") or {}).get(aux_key)
        if isinstance(slot, dict):
            slot["base_url"] = prev.get("base_url", "")
    result["backup"] = backup_file(HERMES_CONFIG, suffix=".headroom")
    save_yaml(HERMES_CONFIG, hermes_cfg)
    _clear_snapshot(settings)
    result["action"] = "restored_direct"
    result["proxied_after"] = False


def _reconcile_enable(
    settings: dict, hermes_cfg: dict, result: dict, dry_run: bool
) -> None:
    proxied = result["proxied_before"]

    if str(settings["upstream"].get("mode", "provider")) != "provider":
        result["warnings"].append(
            "upstream.mode != provider is not managed by ops-kit yet — "
            "keeping the direct route"
        )
        if proxied:
            _restore_direct(settings, hermes_cfg, result, dry_run)
        return

    provider, entry = upstream_provider_entry(hermes_cfg, settings)
    upstream_url = str(entry.get("base_url", "") or "").rstrip("/")
    key_env = str(entry.get("api_key_env") or entry.get("key_env") or "").strip()
    result["upstream"] = {"provider": provider, "base_url": upstream_url}
    if not upstream_url or not key_env:
        result["warnings"].append(
            f"primary provider '{provider}' has no OpenAI-compatible "
            f"providers entry (base_url + api_key_env) — cannot proxy it; "
            f"keeping the direct route"
        )
        if proxied:
            _restore_direct(settings, hermes_cfg, result, dry_run)
        return

    up_result = daemon.up(settings, upstream_url, dry_run=dry_run)
    healthy = up_result.get("healthy", False)
    if dry_run and not healthy:
        result["action"] = "would_enable" if not proxied else "would_keep_enabled"
        result["warnings"].append(f"dry-run: {up_result['message']}")
        return
    if not healthy:
        result["warnings"].append(
            f"proxy not healthy ({up_result['message']}) — staying direct"
        )
        if proxied:
            _restore_direct(settings, hermes_cfg, result, dry_run)
        return

    desired_entry = {
        "name": PROVIDER_NAME,
        "base_url": str(settings["base_url"]).rstrip("/"),
        # key_env is what the runtime's named-custom-provider path reads;
        # api_key_env mirrors the convention used by the other entries.
        "key_env": key_env,
        "api_key_env": key_env,
        "managed_by": "hermes-ops-kit",
    }
    model = hermes_cfg.setdefault("model", {})
    current_entry = (hermes_cfg.get("providers") or {}).get(PROVIDER_NAME)
    if proxied and current_entry == desired_entry:
        result["action"] = "already_enabled"
        result["proxied_after"] = True
        return
    if dry_run:
        result["action"] = "would_enable"
        result["proxied_after"] = True
        return

    if not proxied:
        # A leftover providers.headroom entry that *we* wrote (e.g. the
        # operator switched provider via `hermes model`, orphaning the
        # overlay) is not user state — never snapshot/restore it.
        prev_entry = (hermes_cfg.get("providers") or {}).get(PROVIDER_NAME)
        ours = (
            isinstance(prev_entry, dict)
            and prev_entry.get("managed_by") == "hermes-ops-kit"
        )
        snapshot = {
            "model": copy.deepcopy(model),
            "had_headroom_provider": (
                PROVIDER_NAME in (hermes_cfg.get("providers") or {}) and not ours
            ),
            "prev_headroom_entry": (None if ours else copy.deepcopy(prev_entry)),
            "aux": {},
        }
    else:
        snapshot = load_snapshot(settings) or {"model": {}, "aux": {}}

    providers = hermes_cfg.setdefault("providers", {})
    providers[PROVIDER_NAME] = desired_entry
    model["provider"] = PROVIDER_NAME
    # model.default and model.base_url stay untouched: the model slug is
    # passed through the proxy to the upstream unchanged.

    # Opt-in aux routes: only routes whose provider matches the proxied
    # upstream can be carried by this proxy (single upstream per daemon).
    aux_cfg = hermes_cfg.get("auxiliary") or {}
    for aux_key in settings["apply"].get("aux_routes") or []:
        slot = aux_cfg.get(aux_key)
        if not isinstance(slot, dict):
            result["warnings"].append(f"aux route '{aux_key}' not found — skipped")
            continue
        if str(slot.get("provider", "")).strip() != provider:
            result["warnings"].append(
                f"aux route '{aux_key}' uses provider "
                f"'{slot.get('provider')}' ≠ upstream '{provider}' — skipped"
            )
            continue
        snapshot["aux"].setdefault(aux_key, {"base_url": slot.get("base_url", "")})
        slot["base_url"] = str(settings["base_url"]).rstrip("/")

    _write_snapshot(settings, snapshot)
    result["backup"] = backup_file(HERMES_CONFIG, suffix=".headroom")
    save_yaml(HERMES_CONFIG, hermes_cfg)
    result["action"] = "enabled"
    result["proxied_after"] = True


def _reconcile_disable(
    settings: dict, hermes_cfg: dict, result: dict, dry_run: bool
) -> None:
    if not result["proxied_before"]:
        result["action"] = "already_direct"
        return
    _restore_direct(settings, hermes_cfg, result, dry_run)
