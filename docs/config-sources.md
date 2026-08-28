# hermes-ops-kit — configuration sources & authority

Single reference for every file the kit reads or writes, so no command
carries a parallel copy of the truth.

| File | Authority | Notes |
|---|---|---|
| `~/.hermes/config.yaml` | **Routing SSOT** (primary/utility/aux/fallback providers, custom providers) | Read directly by `route-test`, `route manager`, `usage`/`health`; written only by `route manager apply-profile` and headroom `reconcile` (with backups) |
| `~/.hermes/.env` | Secrets (bootstrap + hand-managed) | Loaded by `env.loader.load_dotenv` for **every** CLI subcommand (bridge.main) |
| `~/.hermes/.env.generated` | Secrets rendered from Vaultwarden | Wins over `.env` for shared keys — both on read (`load_dotenv`) and on write (`_merge_generated_into_env` updates in place) |
| `~/.hermes/ops-kit/routes.yaml` | Kit-owned deployed route labels | Falls back to packaged `hermes_ops_kit/config/routes.yaml` |
| `~/.hermes/ops-kit/image_routes.yaml` | Kit-owned deployed image routes | Falls back to packaged copy; `image set-*` writes here |
| `~/.hermes/ops-kit/assistants.yaml` | Deployed assistants registry | **Wins** over packaged `config/assistants.yaml` (route-test reads deployed first) |
| `~/.hermes/ops-kit/{budget,headroom,plugin_policy,obsidian_maintenance}.yaml` | Kit-owned operational state | Each falls back to its packaged default |
| `hermes_ops_kit/config/*` | Packaged defaults only | Never the authority when a deployed file exists |
| `hermes_ops_kit/provider_catalog.py` | **Provider/model + env-key catalog SSOT** | Consumed by `bridge.CAPABILITIES`, `route_verifier` credential map; `PROVIDER_META.preferred_models` and `BUILTIN_PROFILES` must stay subsets (test-enforced in `tests/test_provider_registry_sync.py`) |

Custom providers (`custom:<name>` in `config.yaml`) are dynamic: they are
never hardcoded — route resolution and credential checks read
`custom_providers[].key_env` from the live config.
