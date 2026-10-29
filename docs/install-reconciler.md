# Runtime Installation Reconciler

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
  content is supplementary evidence only. The probe also returns the target's
  installed distributions and its PEP 508 marker environment, which the
  dependency check below needs.
* **Fingerprint** — deterministic SHA-256 over reinstall-relevant
  declarations only (scripts, plugin entry-points, base dependencies;
  PEP 503-normalized). Implementation-only changes (`*.py`, docs, tests)
  do not invalidate it; a same-version entry-point or topology change does.
* **HealthReport** — `HEALTHY | REPAIRABLE | DIAGNOSE_ONLY | UNSAFE` with
  non-exclusive findings: `CONSOLE_ENTRYPOINT_DRIFT`,
  `EDITABLE_TOPOLOGY_DRIFT`, `DEPENDENCY_DECLARATION_DRIFT`,
  `GENERATED_EXECUTABLE_DRIFT`, `INTERPRETER_MISMATCH`,
  `MULTIPLE_INSTALLATIONS`, `RUNTIME_PROBE_FAILURE`,
  `SOURCE_ORIGIN_DISALLOWED`, `MISSING_DEPENDENCY`,
  `INCOMPATIBLE_DEPENDENCY`, `DEPENDENCY_CHECK_UNAVAILABLE`,
  `PREREQUISITE_FAILED`, … Source-origin policy is allowlist-based —
  never inferred from hostname/OS.

## CLI

```bash
hermes-ops-kit install doctor                  # read-only, human-readable
hermes-ops-kit install doctor --json           # pure JSON, schema_version 1
hermes-ops-kit install doctor --verbose        # dist-info + evidence detail
hermes-ops-kit install doctor --python <path>  # inspect an explicit runtime
hermes-ops-kit install doctor --repair         # M2: safe repair + reinspection
```

Exit 0 only when the reported health is `HEALTHY`. The host prerequisites that
`install doctor` checks (Python version, `bw` CLI, `.env` permissions) are
findings (`PREREQUISITE_FAILED`) in that report, so the JSON, the displayed
health and the exit status always agree. Note the contract consequence: `overall` can
now be `DIAGNOSE_ONLY` where it used to read `HEALTHY` while the exit status was 1
(for example when `bw` is missing or `.env` is not mode 600). The top-level keys and
`schema_version` are unchanged; only `findings` and `overall` carry the new information.

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
sudo — `uv pip -p <python>` when available, else `<python> -m pip`, always with
`--no-deps` (a repair is packaging-only: it never resolves, installs or
upgrades dependencies). The source is canonicalized to an absolute path (no
implicit source switching).

An installer exit 0 does **not** mean success (`repair.py`): every repair is
followed by a mandatory fresh discover → evaluate → probe reinspection, and
success is declared only if the result is HEALTHY. The repair is idempotent
(a second `--repair` on a healthy runtime is a no-op) and serialized by the
`~/.hermes/locks/install-reconciler.lock` advisory lock (reuses
`security/lockfile.py`). A pre-repair snapshot (git SHA, installed version,
origin, runtime) is captured for M3 rollback.

## Ownership guard

Only an environment that is provably outside every Hermes-owned location is
modified directly. `classify_ownership()` (`discovery.py`) records
`runtime_role` and `ownership_reason` in the runtime context:

* `managed` — the target's environment prefix, as written or resolved, is
  core's committed environment (when core's `pm` is loaded in the process),
  or lies anywhere under `~/.hermes/installs` (committed environments and the
  package-manager runtime), under `~/.hermes/tools` (the managed Python store,
  which includes the base interpreters), is a `venv`/`.venv` directly in
  `~/.hermes`, or is the core source tree's `venv`/`.venv`
  (`HERMES_AGENT_SOURCE`, `HERMES_INSTALL_ROOT`, `~/.hermes/hermes-agent`).
  Containment is decided by filesystem identity (`samefile` on each ancestor),
  not by comparing path strings, so a case-variant spelling on a case-insensitive
  filesystem, a symlink or a hardlink cannot hide inside these locations;
* `standalone` — outside all of those;
* `unknown` — the interpreter cannot be found or core's own record is
  unreadable. Treated like `managed`; the role is never assumed.

`managed` and `unknown` targets are refused by the planner, by `repair` (which
re-derives ownership before it replaces any PATH shim), by `install update`
(before any fetch, pull or install; recorded as `stopped_at: ownership`) and by
`run_install` itself, which re-derives ownership at call time so a stale or
hand-built plan cannot reach the installer. A refusal is a normal result
(`refused`, nothing launched). The message points at native maintenance —
`hermes plugins update hermes-ops-kit`, `hermes pm install` or `hermes update` —
which ops-kit never invokes itself. Inside Hermes, installer children get core's
scoped child environment; if core cannot provide one, that is a refusal too.

## Dependency health

`DEPENDENCY_DECLARATION_DRIFT` compares declarations with `pyproject.toml`. The
resolved check asks the `pip check` question: for the requirements the installed
distribution declares, is a compatible distribution installed in the **target**?
Requirements are evaluated with `packaging` against the target's marker
environment (not the host's); requirements whose marker is false for the target,
and unselected extras, are not required. Results are `MISSING_DEPENDENCY` and
`INCOMPATIBLE_DEPENDENCY` (errors, not repairable: a packaging-only reinstall
cannot fix them, so the planner blocks repair). If `packaging` is unavailable
the result is an informational `DEPENDENCY_CHECK_UNAVAILABLE` — unknown is never
reported as healthy and never crashes the reconciler. The same finding is reported
when the target's marker environment is incomplete (the evaluator refuses to fill
gaps from the host), and malformed probe entries are ignored. The preflight fast check
runs the same evaluation in-process from `importlib.metadata`, with no
subprocess. The installed-package map and marker environment are not part of the
JSON report; only findings are.

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
  `git pull` exit code alone is never treated as update success. A
  `managed` or `unknown` target stops the update at `ownership`, before the
  sync step (see Ownership guard).

Follow-up: remote hosts move from editable git checkouts to immutable
versioned artifacts.
