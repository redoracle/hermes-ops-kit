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

Custom providers (`custom:<name>` in `config.yaml`) are dynamic: they are
never hardcoded — route resolution and credential checks read
`custom_providers[].key_env` from the live config.

## Loaders (consolidated)

- **`.env` / `.env.generated`** — sole parser: `env/loader.py` (`load_dotenv()`,
  `load_env_dict()`, `parse_env_file()`, `load_env_setdefault()`). Generated
  wins over `.env`; inline `#` comments require preceding whitespace
  (python-dotenv semantics). JSON envelopes report the kit version, and every `--json` mode uses the standard `ok_envelope` shape (ok/command/version/timestamp/result/errors) — test-enforced. `env/env_loader.py` is a compat shim only.
  `usage_metrics_v2.load_env_file` delegates to `load_env_setdefault()`
  (never clobbers real env vars).
- **`HERMES_HOME`** — sole path authority: `ops_config_io.HERMES_HOME` /
  `expand_home()` / `hermes_config()`. No module constructs `~/.hermes`
  paths or reads `HERMES_HOME` from the environment directly
  (test-enforced: `tests/test_config_single_source.py`, including `scripts/*.sh`).
- **YAML reads** — via `ops_config_io.load_yaml` (ruamel → PyYAML → JSON,
  `{}` on missing/empty/unparseable/non-mapping roots; `deployed_or_bundled`
  derives its dir from `HERMES_HOME` at call time; all atomic writers use
  unique `mkstemp` temps with `finally` cleanup). `policy/engine.py` and
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
