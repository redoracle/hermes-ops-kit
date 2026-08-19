---
name: config-reconciliation
description: Pattern for writing a new config.yaml reconciler — envelope contract, safety invariants, dry-run, idempotency, and sub-reconciler composition.
---

# Config Reconciliation

Pattern for building a reconciler that aligns `~/.hermes/config.yaml` (or any ops-kit config
file) with a desired state. Distilled from the two reconcilers in this repo:
`headroom_ops/reconcile.py` (route overlay) and `security/plugin_scanner/enforce.py` (plugin
approvals), which both write to `config.yaml` using the same contract.

## When to use this pattern

You need this pattern whenever a new feature must **mutate a shared config file** —
`config.yaml`, `headroom.yaml`, `image_routes.yaml`, or `plugin_policy.json`. If you write
directly to the file without following the contract, you risk:

- Corrupting the file (partial write on crash)
- Losing the user's hand-edited formatting/comments
- Overwriting another reconciler's changes
- No rollback path when the mutation breaks Hermes
- Silent failure that goes undetected

## The contract

Every reconciler returns the same envelope. Callers never catch exceptions — the reconciler
folds everything into the envelope.

```python
result: dict[str, Any] = {
    "ok": True,              # False ONLY on invariant violations or
                             # unreadable config — never on transient
                             # proxy/network issues
    "action": "noop",        # Past-tense verb describing what happened:
                             # noop, enabled, restored_direct,
                             # already_enabled, already_direct, skipped
    "warnings": [],          # Non-fatal: proxy won't start, upstream
                             # drift, missing aux route. Route stays
                             # direct — Hermes still works.
    "errors": [],            # Fatal invariant violations: collisions,
                             # unreadable config. Refuse to mutate.
    "backup": None,          # Path to timestamped .bak, or None
    "dry_run": False,        # Echoed from input
    # ... reconciler-specific fields ...
}
```

Key rule: **`ok: False` means the operator must fix something before the reconciler can
proceed** (e.g., a fallback URL that points at the proxy itself). `ok: True` with non-empty
`warnings` means "I did my best — Hermes still works, but there's something you should know."

## Safety invariants (do not skip any of these)

### 1. Backup before every write

```python
# Timestamped, never overwrites previous backups
result["backup"] = backup_file(HERMES_CONFIG, suffix=".headroom")
save_yaml(HERMES_CONFIG, hermes_cfg)
```

`backup_file()` creates `config.yaml.bak.20260714-120349.headroom` — timestamp means no two
backups ever collide, even within the same second.

### 2. Snapshot pre-change state for exact rollback

Before mutating, save enough state to restore the exact previous configuration:

```python
snapshot = {
    "model": copy.deepcopy(model),         # provider, default, base_url
    "had_headroom_provider": bool,         # was the overlay entry ours?
    "prev_headroom_entry": {...} | None,   # preserve user's entry, discard ours
    "aux": {route_key: {base_url: ...}},   # per-aux-route original base_urls
}
_write_snapshot(settings, snapshot)
```

The snapshot distinguishes **user state** (preserve on restore) from **reconciler-managed
state** (discard on restore). A `managed_by` key on the entry is one way to tell them apart.

### 3. Atomic write (tmp → chmod 600 → fsync → replace)

Never write directly to the target path. Use `ops_config_io.save_yaml()` (or the equivalent
`tempfile.mkstemp` + `os.replace` sequence):

```python
# ops_config_io.py path (preferred — shared utility)
save_yaml(path, data)

# Raw path (if you can't use ops_config_io)
fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=config_dir)
os.fchmod(fd, 0o600)
with os.fdopen(fd, "w") as f:
    yaml.dump(config, f)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, target_path)
```

### 4. Dry-run: compute everything, mutate nothing

```python
if dry_run:
    result["action"] = "would_enable"
    result["proxied_after"] = True
    return  # <-- return BEFORE any write, any snapshot, any daemon start

# ... only now do real work ...
_backup = backup_file(HERMES_CONFIG)
_write_snapshot(settings, snapshot)
save_yaml(HERMES_CONFIG, hermes_cfg)
```

### 5. Collision / invariant detection before any mutation

Check for illegal states that would make the system worse, and refuse to proceed:

```python
collisions = collision_findings(hermes_cfg, settings)
if collisions:
    result["ok"] = False
    result["errors"].extend(collisions)
    result["errors"].append(
        "refusing to reconcile: fix the entries above — "
        "fallbacks must stay direct or graceful degradation is lost"
    )
    return result  # <-- exit BEFORE backup, snapshot, or write
```

The check runs before the first mutation. The error message tells the operator **exactly**
what to fix and why.

### 6. "Never raises" for non-critical paths

```python
def reconcile(dry_run: bool = False) -> dict:
    try:
        settings = load_settings()
        # ... main logic ...
    except Exception as exc:
        result["ok"] = False
        result["errors"].append(f"unexpected reconcile error: {exc}")
    return result
```

The top-level function catches everything. A bug in the reconciler must never propagate an
unhandled exception to the caller — especially when the caller is a preflight check that
controls whether Hermes boots (exit code 0/2/3 must not become a traceback).

## Idempotent actions

Every action must be safe to run repeatedly. The reconciler detects its own prior work and
returns a no-op:

| State before | Desired | Action | What happens |
|---|---|---|---|
| Direct route | Enabled | `enabled` | Snapshot → health-check → overlay applied |
| Proxied route | Enabled | `already_enabled` | No mutation (providers entry + model.provider match desired) |
| Proxied route | Disabled | `restored_direct` | Snapshot restored → providers.headroom removed |
| Direct route | Disabled | `already_direct` | No mutation |
| Broken state | Enabled | `warnings` + direct route | Proxy won't start → route stays direct → warn operator |

Detection logic for "already in desired state":

```python
# headroom — check that the providers entry matches exactly
current_entry = (hermes_cfg.get("providers") or {}).get(PROVIDER_NAME)
if proxied and current_entry == desired_entry:
    result["action"] = "already_enabled"
    return

# enforce — check that enabled/disabled lists already match
if not has_changes:
    return {"applied": False, ...}
```

## Composing reconcilers

When one reconciler runs inside another, the sub-reconciler is **best-effort only** — its
failure must never affect the parent's exit code or result contract:

```python
# security/plugin_scanner/enforce.py:main()
# Headroom runs AFTER plugin enforcement. If the proxy won't start,
# the route stays direct — preflight exit codes are unchanged.
headroom_result = _reconcile_headroom(dry_run=args.dry_run)

def _reconcile_headroom(dry_run: bool = False) -> dict:
    try:
        from headroom_ops.reconcile import reconcile
        return reconcile(dry_run=dry_run)
    except Exception as exc:
        return {
            "ok": False,
            "action": "skipped",
            "errors": [f"headroom reconcile unavailable: {exc}"],
        }
```

Rules for composing:
1. **Order matters** — security-first (enforce) before routing (headroom)
2. **Sub-reconciler is non-blocking** — its failure folds into a warning/error in the
   sub-result, not the parent's exit code
3. **Don't interleave writes** — let each reconciler finish its full cycle (backup →
   snapshot → write → verify) before starting the next
4. **Separate backups** — each reconciler creates its own timestamped `.bak` with a
   distinct suffix (`.headroom`, `.preflight`)

## Shared I/O (don't inline YAML load/save)

Use `ops_config_io.py` for all config file I/O. It handles:

- **ruamel.yaml → PyYAML → JSON fallback chain** — preserves comments when ruamel is available
- **Atomic writes** — temp → chmod 600 → os.replace
- **Timestamped backups** — `backup_file(path, suffix=".headroom")`
- **Standard paths** — `HERMES_CONFIG`, `OPS_KIT_DIR`

```python
from ops_config_io import HERMES_CONFIG, backup_file, load_yaml, save_yaml

hermes_cfg = load_yaml(HERMES_CONFIG)       # {} on missing/unparseable
result["backup"] = backup_file(HERMES_CONFIG, suffix=".my-feature")
save_yaml(HERMES_CONFIG, hermes_cfg)
```

Before `ops_config_io.py` existed, `enforce.py` had its own `_parse_hermes_config()`,
`_save_hermes_config()`, and `_backup_hermes_config()` — all duplicates of what
`reconcile.py` also needed. The refactoring in this commit extracted the common path.
Don't add a third copy.

## Checklist for a new reconciler

Before landing a PR that adds a new config.yaml writer, verify:

- [ ] Returns the standard envelope `{ok, action, warnings, errors, backup, dry_run}`
- [ ] Backup before every write (timestamped, never overwrites)
- [ ] Snapshot pre-change state for exact rollback
- [ ] Atomic write (tmp → chmod 600 → replace)
- [ ] Dry-run computes everything, mutates nothing, returns early
- [ ] Collision/invariant detection before the first mutation
- [ ] All actions are idempotent (safe to run twice)
- [ ] Top-level function catches all exceptions (never raises)
- [ ] Uses `ops_config_io` for YAML load/save/backup (not inline)
- [ ] If composing with another reconciler, sub-reconciler is best-effort only
- [ ] `ok: False` only for invariant violations (not transient issues)
- [ ] Warnings carry actionable messages (operator can fix without reading code)
- [ ] `managed_by` marker on entries the reconciler owns (so snapshot logic can
      distinguish user state from managed state)
- [ ] Tested with: existing config, missing config, malformed config, dry-run,
      already-in-desired-state, collision state, and sub-reconciler failure

## Real examples in this codebase

### headroom_ops/reconcile.py — Route overlay reconciler

- **Desired state**: `headroom.yaml` (`enabled: true/false`)
- **Actual state**: `config.yaml` (`model.provider` + `providers.headroom`)
- **Safety**: `fallback_providers` are never touched → graceful degradation
- **Snapshot**: pre-enable `model` + `providers.headroom` + aux `base_url`s
- **Collision guard**: fallback/provider entries pointing at `127.0.0.1:<proxy-port>`
- **Idempotency**: `already_enabled`, `already_direct`
- **Composed by**: `enforce.py` via `_reconcile_headroom()` (best-effort)

### security/plugin_scanner/enforce.py — Plugin approval reconciler

- **Desired state**: `plugin_policy.json` (scan results + operator approvals)
- **Actual state**: `config.yaml` (`plugins.enabled` + `plugins.disabled`)
- **Safety**: only touches plugins it scanned — preserves hand-curated entries
- **No snapshot needed**: plugin enabled/disabled lists are directly computed from
  scan results (no rollback of a previous overlay)
- **Idempotency**: computes delta, returns `has_changes: false` when already synced
- **Composes**: calls headroom `reconcile()` after its own write

## Anti-patterns (what NOT to do)

| Anti-pattern | Why it breaks | Fix |
|---|---|---|
| Writing directly to config.yaml without backup | Crash during write = corrupted config | Always `backup_file()` first |
| `open(path, "w").write(...)` without fsync+replace | Partial write on crash, readers see empty file mid-write | temp file + fsync + os.replace |
| Raising exceptions from a reconciler | Caller (preflight) crashes with traceback instead of controlled exit code | Catch all, fold into result envelope |
| Skipping dry-run check and mutating anyway | `--dry-run` writes to disk, operator loses trust | Early return before any mutation |
| No collision check before mutation | Reconciler creates a circular dependency (fallback → proxy → proxy) | Detect + refuse + tell operator why |
| Overwriting user's hand-edited config entries | Operator's `providers.custom` entry silently removed | Snapshot user state, only touch managed entries |
| Using `ok: False` for transient failures | Preflight blocks Hermes boot because proxy was slow to start | Use `warnings` for transient; `ok: False` only for invariants |

## Related patterns

- [[credential-leak-audit]] — sanitizing URLs before they reach display/log output
  (reconcilers that log or display config values must sanitize them first)
- [[review-response]] — systematic review feedback workflow (new reconcilers should
  be reviewed against this checklist)
- [[rebase-conflict]] — resolving conflicts when upstream refactored the config path
  (e.g., upstream extracted YAML helpers that your reconciler branch also modified)
