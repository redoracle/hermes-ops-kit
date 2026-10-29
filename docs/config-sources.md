# hermes-ops-kit — configuration sources & authority

Single reference for every file the kit reads or writes, so no command
carries a parallel copy of the truth.

| File | Authority | Notes |
|---|---|---|
| `~/.hermes/config.yaml` | **Routing SSOT** (primary/utility/aux/fallback providers, custom providers) | Read via `ops_config_io.hermes_config()` (dynamic — honors `HERMES_HOME` monkeypatching); written by `route manager set-primary/set-aux/fallback*/apply-profile`, headroom `reconcile`, and `install repair` (all atomic, with backups) |
| `~/.hermes/.env` | Secrets (bootstrap + hand-managed) | Loaded by `env.loader.load_dotenv` for **every** CLI subcommand (bridge.main) |
| `~/.hermes/.env.generated` | Secrets rendered from Vaultwarden | Wins over `.env` for shared keys — both on read (`load_dotenv`) and on write (`_merge_generated_into_env` updates in place, atomically) |
| `~/.hermes/ops-kit/routes.yaml` | Kit-owned deployed route labels | Falls back to packaged `hermes_ops_kit/config/routes.yaml`; route *profiles* live in `BUILTIN_PROFILES` (`hermes_route_manager.py`) — the `profiles:` block in routes.yaml is not read by `apply-profile` |
| `~/.hermes/ops-kit/image_routes.yaml` | Kit-owned deployed image routes | Falls back to packaged copy; `image set-*` writes here (canonical `save_yaml` — always YAML); `policies.output_dir` is honored at generation time (expand_home-normalized) |
| `~/.hermes/ops-kit/assistants.yaml` | Deployed assistants registry | **Wins** over packaged `config/assistants.yaml` (route-test reads deployed first) |
| `~/.hermes/ops-kit/{budget,headroom,plugin_policy,obsidian_maintenance}.yaml` | Kit-owned operational state | Each falls back to its packaged default |
| `hermes_ops_kit/config/*` | Packaged defaults only | Never the authority when a deployed file exists |
| `hermes_ops_kit/provider_catalog.py` | **Provider/model + env-key catalog SSOT** | Consumed by `bridge.CAPABILITIES`, `route_verifier` credential map; `PROVIDER_META.preferred_models` and `BUILTIN_PROFILES` must stay subsets (test-enforced in `tests/test_provider_registry_sync.py`) |
| `hermes_ops_kit/provider_keys.py` | **Which `providers:` / `custom_providers:` block (endpoint, key env) Hermes uses for a provider name** | Mirrors core's `custom_provider_aliases`; used by `doctor`, `route_verifier`, `route_runtime_harness` and the Headroom overlay so they cannot disagree (`tests/test_provider_keys.py`, `tests/test_headroom_upstream.py`) |

Custom providers (`custom:<name>` in `config.yaml`) are dynamic: they are
never hardcoded — route resolution and credential checks read
`custom_providers[].key_env` from the live config.

## Loaders (consolidated)

- **`.env` / `.env.generated`** — sole parser: `env/loader.py` (`load_dotenv()`,
  `load_env_dict()`, `parse_env_file()`, `load_env_setdefault()`). Generated
  wins over `.env`; values follow python-dotenv syntax: optional `export`, quoted
  values with escapes, multiline quoted values, and `#` comments after whitespace.
  Interpolation is off, so `${VAR}` is never expanded from the ambient environment.
  A line without `=` or a malformed line yields no key. JSON envelopes report the kit version, and every `--json` mode uses the standard `ok_envelope` shape (ok/command/version/timestamp/result/errors) — test-enforced. `env/env_loader.py` is a compat shim only.
  `usage_metrics_v2.load_env_file` delegates to `load_env_setdefault()`
  (never clobbers real env vars). Inside Hermes (embedded) none of these loaders
  writes `os.environ`: the process environment is shared by every profile, so
  embedded code reads credentials with `env.loader.get_secret()` (or
  `provider_catalog.provider_key()`) instead — the active profile's `.env` /
  `.env.generated` first (generated wins), then core's scoped `get_secret`. A read made
  with no profile scope bound while Hermes multiplexes profiles raises core's
  `UnscopedSecretError` and must not be swallowed. Standalone, the process
  environment is the last fallback, as before. The files deliberately come first:
  ops-kit's own rotation writes `.env.generated`, which must win over an older `.env`
  entry and may not be visible to core's scope; a value that core's scope holds only
  for an external SecretSource is reached when no file has the key. An empty entry
  (`KEY=`) counts as unset, never as an override.
- **`HERMES_HOME`** — sole path authority: `ops_config_io.home()`, resolved **at call
  time** (an explicit `ops_config_io.HERMES_HOME` override, then core's
  `get_hermes_home()` when core is loaded — so routed profiles get their own home — then
  the `HERMES_HOME` environment variable, then `~/.hermes`), with `config_path()` /
  `expand_home()` / `hermes_config()` / `ops_kit_dir()` built on it. The module-level
  names (`HERMES_HOME`, `HERMES_CONFIG`, `OPS_KIT_DIR` and per-module constants such as
  `AUDIT_PATH`, `LOCK_DIR`, `CACHE_DB_PATH`) remain only as deprecated import-time
  aliases for external code; the kit never reads them (test-enforced:
  `tests/test_no_frozen_paths.py`, `tests/test_config_single_source.py`, including
  `scripts/*.sh`; the guard reads the syntax tree, so renamed imports are caught too).
  No module builds a `~/.hermes` path itself, apart from string defaults handed straight
  to `expand_home()`; `ops_config_io.default_home()` is the one place that spells the
  shared default root.
  Audit and task stores, locks, scanner cache and policy, MCP policy, rotation
  checkpoints, exports, assistants manager, headroom reconciler, install reconciler,
  skill factory and security bootstrap all follow the active profile, and
  `tests/test_profile_concurrency.py` runs three profiles concurrently against them.
- **Child processes** — every `subprocess` call passes an explicit `env=`
  (test-enforced: `tests/test_spawn_env.py`). A child that acts for the profile gets
  `_subprocess.child_env()`: inside Hermes, core's served-profile environment
  (launch-profile credentials scrubbed for a routed profile, the profile's own overlaid
  where credentials are needed), standalone a copy of the parent environment; either way
  with `HERMES_HOME` set so the child resolves the same profile. Credential-free tools
  (the scanner's semgrep, bandit and gitleaks, `git rev-parse` on a plugin, the install
  probe) get `minimal_env()`. The guard resolves `import subprocess as x` and
  `from subprocess import run`, and rejects an `env=` that merely copies `os.environ`.
- **Read-only mode** — `HERMES_OPS_KIT_READ_ONLY=1` (`ops_config_io.read_only()`), set
  only by the `/ops-kit` chat child: defaults are not seeded into the profile and MCP
  servers are not started or contacted (`run_audit` forces `dynamic_discovery=False`).
- **YAML reads & Atomic writes** — via `ops_config_io.load_yaml` (ruamel → PyYAML → JSON,
  `{}` on missing/empty/unparseable/non-mapping roots; `deployed_or_bundled`
  derives its dir from `HERMES_HOME` at call time; all atomic writers use
  unique `mkstemp` temps with `os.fsync`, `fchmod` (0o600 for secrets/backups), and atomic `os.replace` with `finally` cleanup). `policy/engine.py` and
  `plugin_scanner/bootstrap._ops_kit_version` also use `load_yaml` (packaged
  files, same fail-open semantics); `security/credential_read_guard.py`
  probes the raw `HERMES_HOME` env deliberately (profile-mode dual root). Fail-closed callers
  use `ops_config_io.load_yaml_strict` (raises `ConfigError`); the
  security-critical loaders in `plugin_scanner/enforce.py` and
  `mcp_auditor/auditor.py` keep their own raising implementations
  deliberately. JSON state/checkpoint writes go through
  `env/atomic_write.atomic_write_json` (temp → chmod 600 → rename), as does
  the `.env` merge in key rotation (`atomic_write`). `usage_metrics_v2`
  keeps its built-in tokenizer only as a no-YAML-dependency last resort.
  Because `load_yaml` returns `{}` for missing and unparseable alike,
  `doctor` flags "exists but empty/unparseable" explicitly so a corrupt
  `config.yaml` never makes the report look healthier. Known limitation:
  `cost_governor/budget.py` still reverts silently to `DEFAULT_BUDGET` on a
  corrupt `budget.yaml`.
- **Deployed-vs-bundled** — `ops_config_io.deployed_or_bundled(name, seed=…)`
  for every ops-kit config (headroom, budget, obsidian_maintenance, routes,
  image_routes, assistants; plugin_scanner bootstrap keeps its own seeding
  that reports creation).
- **Provider truth** — `provider_catalog.py`: models, env keys (incl.
  `fal`/`cloudflare` image backends), base URLs, copilot catalog, alias
  normalization (`PROVIDER_ALIASES`), and the credential resolvers
  `key_envs_for` / `first_available_key` / `has_credential` — every consumer
  (adapters, rotators, usage probes, image router) resolves through these;
  env-var name literals are test-banned outside the catalog. Adapters, rotators, usage probes, doctor credential checks
  and budget classes all derive from it (test-enforced subset invariant:
  `tests/test_provider_registry_sync.py::test_providers_subset_of_catalog`).
  *Capabilities Seam Note:* `bridge.CAPABILITIES` describes command dispatch semantics and metadata per provider, while `provider_catalog.py` is the data authority for model names, base URLs, credentials, and aliases. `bridge.CAPABILITIES` derives its model lists directly from `provider_catalog.PROVIDER_MODELS` (pinned by `tests/test_provider_registry_sync.py`).


### Override env vars (documented, one default each)

| Var | Authority | Default | Used by |
|---|---|---|---|
| `HERMES_HOME` | `ops_config_io.HERMES_HOME` | `~/.hermes` | every path in the kit |
| `HERMES_ASSISTANTS_CONFIG` | explicit assistants.yaml path override | `$HERMES_HOME/ops-kit/assistants.yaml` | `assistants/registry.py`, `hermes_assistant_manager.py` |
| `HERMES_PLUGIN_POLICY_PATH` | explicit plugin_policy.json override | `$HERMES_HOME/ops-kit/plugin_policy.json` | `security/plugin_scanner/policy.py` |
| `ASSISTANT_TIMEOUT_SECONDS` | default for `assistant ping --timeout` | `15` | `hermes_assistant_manager.py` |
| `HERMES_TEST_MODE` | test fixtures in usage output | unset | `usage_metrics_v2.py` |
| `SKIP_RATELIMIT_PROBE` | skip live ratelimit probing | unset | `usage_metrics_v2.py` |
