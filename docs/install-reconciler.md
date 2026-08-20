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
```

Exit 0 only when the runtime is coherent with the source declarations.

## Tests

`tests/test_install_reconciler/` — unit (fingerprint, evaluator scenarios,
resolver, serialization) plus `integration`-marked real-venv tests that
reproduce the original incident (`bridge:main` editable install → source
repackaged without reinstall → drift detected; runtime provably broken) and
the negative case (.py-only change → no drift).

## Roadmap

* **M2** — RepairPlanner + InstallerAdapter (`uv pip -p` / `python -m pip`,
  argv-only, no shell), `install doctor --repair` with mandatory
  reinspection and idempotence. Auto-repair denied on: dependency drift,
  source mismatch, ambiguous interpreter, disallowed origin.
* **M3** — read-only fast check wired into `preflight`; transactional
  updater (LOCK → snapshot → sync → reconcile → validate → UNLOCK) with
  rollback or explicit degraded state. Remote hosts move from editable
  git checkouts to immutable versioned artifacts (follow-up).
