# Runtime Installation Reconciler (M1 — detect-only)

## Why

A previous editable install of `hermes-ops-kit` materialized a console script
importing `from bridge import main`. The repackage moved `bridge.py` →
`hermes_ops_kit/bridge.py` with entry-point `hermes_ops_kit.bridge:main`. A
`git pull` of the source did **not** reconcile:

```
source state  ≠  installed distribution metadata  ≠  generated executables  ≠  runtime
```

Result: `ModuleNotFoundError: No module named 'bridge'`. The same class of
drift was observed on remote hosts (wrapper shims pointing at deleted files).

## Principle

> **Detect broadly, repair narrowly.**

An update succeeded only when the Python runtime Hermes will use is coherent
with the updated source — not when `git pull` returned 0.

## Architecture

```
RuntimeContext (explicit target interpreter — never assumes sys.executable)
    ├── discover_actual_state() → ActualInstallation   (facts only)
    ├── resolve_expected_state() → ExpectedInstallation (pyproject.toml)
    └── evaluate(actual, expected) → HealthReport       (pure data)
```

* **Discovery** runs one isolated probe: `<target-python> -I -c …`. `-I`
  keeps PYTHONPATH/user-site/cwd out, so results reflect the runtime as
  pip/Hermes sees it. Primary sources: `importlib.metadata` (distribution,
  `direct_url.json`, entry-points), `sysconfig` (scripts dir). The runtime
  authority is a real `EntryPoint.load()` per entry-point. Wrapper shebang
  content is supplementary evidence only.
* **Fingerprint** — deterministic SHA-256 over reinstall-relevant
  declarations only (scripts, plugin entry-points, base dependencies;
  PEP 503-normalized). Implementation-only changes (`*.py`, docs, tests)
  do not invalidate it; a same-version entry-point or topology change does.
* **HealthReport** — `HEALTHY | REPAIRABLE | DIAGNOSE_ONLY | UNSAFE` with
  non-exclusive findings: `CONSOLE_ENTRYPOINT_DRIFT`,
  `EDITABLE_TOPOLOGY_DRIFT`, `DEPENDENCY_DECLARATION_DRIFT`,
  `GENERATED_EXECUTABLE_DRIFT`, `INTERPRETER_MISMATCH`,
  `MULTIPLE_INSTALLATIONS`, `RUNTIME_PROBE_FAILURE`,
  `SOURCE_ORIGIN_DISALLOWED`, … Source-origin policy is allowlist-based —
  never inferred from hostname/OS.

## CLI

```bash
hermes-ops-kit install doctor                  # read-only, human-readable
hermes-ops-kit install doctor --json           # pure JSON, schema_version 1
hermes-ops-kit install doctor --verbose        # dist-info + evidence detail
hermes-ops-kit install doctor --python <path>  # inspect an explicit runtime
hermes-ops-kit install doctor --repair         # M2: safe repair + reinspection
```

Exit 0 only when the runtime is coherent with the source declarations.

## Tests

`tests/test_install_reconciler/` — unit (fingerprint, evaluator scenarios,
resolver, serialization) plus `integration`-marked real-venv tests that
reproduce the original incident (`bridge:main` editable install → source
repackaged without reinstall → drift detected; runtime provably broken) and
the negative case (.py-only change → no drift).

## Repair (M2)

`install doctor --repair` runs only after the **RepairPlanner** approves a
**RepairPlan** (`planner.py`). Auto-repair is allowed only for packaging-only
drift: same allowed source, same explicit target interpreter, exactly one
installation, dependency declaration unchanged, no unrecognized findings.
Denied (no mutation, reason printed) on: dependency drift, multiple
installations, interpreter mismatch/ambiguity, disallowed origin, unknown
mode/source, or any overall UNSAFE / DIAGNOSE_ONLY.

Execution (`installer_adapter.py`): argv arrays only — no `shell=True`, no
sudo — `uv pip -p <python>` when available, else `<python> -m pip`. The
source is canonicalized to an absolute path (no implicit source switching).

An installer exit 0 does **not** mean success (`repair.py`): every repair is
followed by a mandatory fresh discover → evaluate → probe reinspection, and
success is declared only if the result is HEALTHY. The repair is idempotent
(a second `--repair` on a healthy runtime is a no-op) and serialized by the
`~/.hermes/locks/install-reconciler.lock` advisory lock (reuses
`security/lockfile.py`). A pre-repair snapshot (git SHA, installed version,
origin, runtime) is captured for M3 rollback.

## M3 — preflight fast check + transactional updater

Implemented (`fastcheck.py`, `updater.py`):

* **Preflight fast check** — step 0 of `hermes-ops-kit preflight`:
  in-process `importlib.metadata` discovery in the current interpreter
  (no pip/uv/network/probe subprocess). Best-effort and never affects
  security exit codes; drift surfaces as a warning pointing at
  `hermes-ops-kit install doctor`.
* **Transactional updater** — `hermes-ops-kit install update [--dry-run]
  [--python <path>]`: LOCK → capture previous state (git SHA, dist
  metadata, runtime ctx) → sync source (`fetch` + `pull --ff-only`;
  STOP on dirty tree; no reset/force) → inspect (doctor) → reconcile
  (planner-gated repair, same lock) → validate (doctor must be HEALTHY)
  → record JSONL ledger (`~/.hermes/ops-kit/update_log.jsonl`, no
  secrets) → UNLOCK. On validation failure the host is left in an
  explicit degraded state with a recorded reason; safe rollback
  (`git checkout <old SHA>`) only when the tree is still clean. The
  `git pull` exit code alone is never treated as update success.

Follow-up: remote hosts move from editable git checkouts to immutable
versioned artifacts.
