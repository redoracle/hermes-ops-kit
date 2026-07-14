# Headroom Integration — Route Overlay Managed by ops-kit

> Design spec. Operator guide (purpose, configuration, administration,
> troubleshooting): [Headroom.md](Headroom.md).

Headroom (PyPI: `headroom-ai`) is an OpenAI-compatible compression proxy that
sits between Hermes and its primary LLM provider, reducing token usage via
transport-level compression, semantic caching, and persistent memory.

ops-kit manages Headroom as a **reconciled route overlay** — not a provider
in its own right, not a second entrypoint, and not a hard dependency.

```
hermes (primary route)
  → providers.headroom (http://127.0.0.1:8790/v1)
  → headroom proxy --openai-api-url <primary provider base_url>
  → real provider (e.g. https://integrate.api.nvidia.com/v1)

fallback_providers (github → deepseek → anthropic → openai)
  → ALWAYS direct, never proxied      ← "Hermes never dies"
```

## Robustness contract

1. **`fallback_providers` are never written.** If the proxy dies, Hermes
   core's own retry/fallback machinery degrades to direct providers.
2. **Health before route.** The proxied route is applied only after the
   proxy answers `/readyz`; otherwise the route stays (or reverts to)
   direct, with a warning — never an error, never a blocked boot.
3. **Collision guard.** Reconciliation refuses to run if any fallback or
   provider entry points at `127.0.0.1:<port>` (that would route the
   degradation path through the dead proxy itself).
4. **Exact rollback.** The pre-enable route is snapshotted
   (`~/.hermes/ops-kit/headroom_prev_route.json`); every config write is
   preceded by a timestamped `config.yaml.bak.*` backup.

## Desired state + reconciliation

Desired state lives in `~/.hermes/ops-kit/headroom.yaml` (`enabled: true|false`,
seeded from the bundled `config/headroom.yaml`). `config.yaml` always reflects
**verified reality**, never an intention:

- `hermes-ops-kit headroom enable` → desired=on + reconcile now.
- `hermes-ops-kit preflight` → after security enforcement, runs a
  best-effort reconcile (the gateway is typically restarted right after:
  `hermes-ops-kit preflight && hermes gateway restart`). The reconcile
  step **never** changes the preflight security exit codes (0/2/3).
- On a host without Headroom (e.g. a remote VPS) the route simply stays
  direct — no host allowlists needed.

## Overlay mechanism (verified against the Hermes runtime)

`model.provider: headroom` plus a named entry:

```yaml
providers:
  headroom:
    base_url: http://127.0.0.1:8790/v1
    key_env: NVIDIA_API_KEY # the UPSTREAM provider's key env
    api_key_env: NVIDIA_API_KEY
    managed_by: hermes-ops-kit
```

Hermes resolves named custom providers as OpenAI-compatible endpoints
(`_resolve_named_custom_runtime`). Headroom forwards the client's
`Authorization` header to the upstream, so `key_env` reuses the upstream
provider's key — no extra key material. `model.default` is untouched: the
model slug passes through the proxy unchanged. The fallback path
(`try_activate_fallback`) resolves each fallback entry independently and
never inherits the primary's endpoint.

## Daemon lifecycle (self-contained, distributable)

No shell aliases, no external supervisors. `headroom_ops/daemon.py` owns:

- `headroom up` — idempotent: health-check first, spawn only when down;
  pidfile/meta/log in `~/.hermes/ops-kit/run/`.
- `headroom down` — pidfile TERM → KILL, cleanup.
- Launch flags enforce the **no-coding profile**: `--no-code-aware` is
  always appended; `--code-aware`, `--code-graph`, `--learn` are stripped
  (`headroom_ops/settings.py:FORBIDDEN_FLAGS`). Headroom is used for
  proxy/compression, retrieve, memory, stats — never code intelligence.

`install.sh` installs `headroom-ai[proxy]` (pipx preferred, pip fallback,
non-fatal). The `[proxy]` extra (fastapi/uvicorn) is required for the
`proxy` subcommand to bind. **LiteLLM is intentionally not installed or
managed**:
`upstream.mode: provider` (default) forwards straight to the primary
provider's endpoint. `upstream.mode: litellm` is reserved for
bring-your-own aggregation setups.

## CLI

```bash
hermes-ops-kit headroom status            # route + daemon overview
hermes-ops-kit headroom doctor [--json]   # health + invariant checks
hermes-ops-kit headroom up | down         # daemon lifecycle only
hermes-ops-kit headroom enable [--dry-run]
hermes-ops-kit headroom disable [--dry-run]
hermes-ops-kit headroom reconcile [--dry-run]
hermes-ops-kit headroom stats             # /stats (token savings)
hermes-ops-kit headroom export            # machine-readable JSON
```

Observability: `hermes-usage` shows a HEADROOM section (desired/actual
route, proxy health, token savings) when relevant; `hermes-route-manager
show|doctor` render the proxied primary as `via headroom(8790) → <provider>`
and treat the ops-kit overlay as expected state (no drift false positives).

## Doctor checks

binary on PATH · `/readyz` · upstream resolvable · upstream key env
present · fallbacks stay direct (collision guard) · fallback chain
non-empty · config matches desired · proxy upstream matches primary
(drift self-heal) · no-coding profile · compression layering note
(Hermes native history compression stays enabled by design;
the aux `compression` LLM route stays direct — `apply.aux_routes` is an
explicit opt-in experiment).

## Provider prerequisite

The primary provider must have an OpenAI-compatible `providers` entry in
`~/.hermes/config.yaml` with `base_url` and `api_key_env`. Without it the
upstream cannot be resolved and reconcile returns `noop`. Providers accessed
through native SDKs (no `base_url`) cannot be proxied.

## Runbook

```bash
# First time — ensure the primary provider has a providers entry
# (base_url + api_key_env) in ~/.hermes/config.yaml, then:
hermes-ops-kit headroom doctor
hermes-ops-kit headroom enable --dry-run
hermes-ops-kit headroom enable
hermes-ops-kit preflight && hermes gateway restart

# Inspect
hermes-ops-kit headroom status
hermes-ops-kit headroom stats
hermes-usage

# Chaos check (Hermes must keep answering via direct fallbacks)
hermes-ops-kit headroom down

# Roll back
hermes-ops-kit headroom disable        # restores the snapshotted route
# last resort:
#   hermes config set model.provider nvidia
#   cp ~/.hermes/config.yaml.bak.<ts>.headroom ~/.hermes/config.yaml
```

## MCP (optional, not part of enable)

The in-chat tools (`headroom_compress` / `headroom_retrieve` /
`headroom_stats`) require registering the Headroom MCP server in
`mcp_servers` — which must then pass `hermes-ops-kit mcp audit` and the
normal approval policy, like any other MCP server.

## Tests

`tests/cli/test_headroom.py` — 11 tests: enable/disable round-trip
(byte-exact restore), idempotent reconcile, dead-proxy degradation,
collision guard, doctor, stats (against an in-process proxy stub),
preflight best-effort isolation, upstream-drift restart, provider-switch
overlay re-application, and hung-proxy force restart.
