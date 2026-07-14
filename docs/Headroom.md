# Headroom — Operator Guide

> Operator guide: purpose, behavior, configuration and administration of the
> Headroom integration managed by hermes-ops-kit.
> Technical design spec: [headroom-integration.md](headroom-integration.md).

## Purpose

[Headroom](https://pypi.org/project/headroom-ai/) is an OpenAI-compatible
proxy that sits between Hermes and its primary LLM provider to reduce token
usage: transport-level compression, semantic caching, and persistent
project memory.

In ops-kit, Headroom is a **reconciled route overlay**:

- it is **not** an LLM provider (no models of its own, no billing);
- it is **not** a second entrypoint (no `hhermes`, no shell aliases);
- it is **not** a dependency: if it is missing or dies, Hermes works
  exactly the same on the direct route.

The profile is **no-coding by design**: the proxy always starts with
`--no-code-aware`; the `--code-aware`, `--code-graph` and `--learn` flags are
stripped regardless of what the configuration says. Headroom serves Hermes
only for compression, memory, stats and observability — never code
intelligence.

## How it works

```
hermes (primary route)
  → providers.headroom (http://127.0.0.1:8790/v1)
  → headroom proxy --openai-api-url <primary provider base_url>
  → real provider (e.g. https://integrate.api.nvidia.com/v1)

fallback_providers (github → deepseek → anthropic → openai)
  → ALWAYS direct, never proxied         ← "Hermes never dies"
```

### One proxy = one upstream (the key point)

The Headroom daemon forwards **everything** to a single upstream endpoint.
Practical consequences:

- **Every model of the upstream provider goes through the proxy.** The model
  slug passes through unchanged: switching models within the same provider
  requires no intervention. In the Hermes model picker, the `headroom` entry
  shows exactly the upstream's `/v1/models` catalog (e.g. NVIDIA's ~120
  models): any of them is served through Headroom.
- **Different providers cannot be aggregated** behind the same proxy. Do not
  pick another provider's slug under the `headroom` entry: the upstream would
  return 404 and Hermes would end up on the fallbacks. Multi-provider
  aggregation would require `upstream.mode: litellm` (reserved, unmanaged: a
  second daemon is a second point of failure).
- **Switching the primary provider moves the proxy's upstream.** See
  "Switching models and providers" below.

### Desired state + reconciliation

The desired state lives in `~/.hermes/ops-kit/headroom.yaml`
(`enabled: true|false`). `config.yaml` always reflects **verified reality**,
never an intention:

- the proxied route is written only after the proxy answers `/readyz`;
- if the proxy does not start, the route stays (or reverts to) direct with a
  warning — never an error, never a blocked boot;
- `hermes-ops-kit preflight` runs a best-effort reconciliation after the
  security enforcement (operational pattern:
  `hermes-ops-kit preflight && hermes gateway restart`); it never changes the
  preflight security exit codes;
- on a host without Headroom (e.g. a remote VPS) the route simply stays
  direct.

**Before enabling:** the primary provider must have an OpenAI-compatible
`providers` entry in `~/.hermes/config.yaml` with `base_url` and
`api_key_env`. Without it the proxy cannot resolve an upstream:

```yaml
providers:
  deepseek:
    api_key_env: DEEPSEEK_API_KEY
    base_url: https://api.deepseek.com/v1
```

Providers accessed through provider-native SDKs (no `base_url`) cannot be
proxied — the reconcile step returns `noop` and the route stays direct.

### Robustness contract

1. `fallback_providers` are **never** written: if the proxy dies at runtime,
   Hermes retries the primary and then degrades on its own to the direct
   fallbacks (a few seconds of retry latency, no outage).
2. **Collision guard**: no fallback or provider entry (other than headroom)
   may point at `127.0.0.1:<port>` — hard error in reconcile and doctor.
3. **Exact rollback**: the pre-enable route is snapshotted
   (`~/.hermes/ops-kit/headroom_prev_route.json`); every config write is
   preceded by a timestamped `config.yaml.bak.*` backup. With ruamel.yaml
   installed the enable→disable round trip is byte-exact, comments included.
4. **Upstream drift self-heals**: if the proxy is healthy but forwarding to
   an endpoint different from the current primary (e.g. after a provider
   switch), reconcile restarts it with the new upstream.

## Configuration

### `~/.hermes/ops-kit/headroom.yaml` (user state, seeded from the bundled default)

```yaml
headroom:
  enabled: false # desired state (reconciled by preflight)
  port: 8790
  base_url: "http://127.0.0.1:8790/v1"
  upstream:
    mode: provider # provider (default) | litellm (reserved)
  proxy_flags:
    [
      "--mode",
      "token",
      "--memory",
      "--memory-storage",
      "project",
      "--no-telemetry",
    ]
  memory_project_root: "~/.hermes"
  apply:
    model: true
    aux_routes: [] # explicit opt-in (e.g. compression) — never default
  startup_timeout_seconds: 20
  run_dir: "~/.hermes/ops-kit/run" # pidfile + meta + log
  state_file: "~/.hermes/ops-kit/headroom_prev_route.json"
```

`enabled` is changed via `headroom enable/disable`, not by hand. The other
knobs can be edited; the next `reconcile` applies them.

### What the overlay writes to `~/.hermes/config.yaml`

```yaml
model:
  provider: headroom # ← the only change to model.*
providers:
  headroom:
    base_url: http://127.0.0.1:8790/v1
    key_env: NVIDIA_API_KEY # the UPSTREAM provider's key
    api_key_env: NVIDIA_API_KEY
    managed_by: hermes-ops-kit
```

`model.default` is untouched. Headroom forwards the client's `Authorization`
header to the upstream, so `key_env` reuses the real provider's key — no
extra key material to manage.

### Installation

`install.sh` installs `headroom-ai[proxy]` automatically (pipx preferred,
pip fallback; non-fatal on failure: it is an optimization, not a
requirement). The `[proxy]` extra is mandatory — without it (fastapi/uvicorn
missing) `headroom proxy` crashes on start with `No module named 'fastapi'`
and the picker shows `headroom (0 models)`. LiteLLM is **not** installed.
Optional extra: `pip install 'hermes-ops-kit[headroom]'`.

## Administration

### CLI

```bash
hermes-ops-kit headroom status               # desired + actual route + proxy health
hermes-ops-kit headroom doctor [--json]      # full checklist (see below)
hermes-ops-kit headroom enable [--dry-run]   # desired=on + reconcile now
hermes-ops-kit headroom disable [--dry-run]  # restore direct route + stop proxy
hermes-ops-kit headroom reconcile [--dry-run]# align config.yaml to desired state
hermes-ops-kit headroom up | down            # daemon lifecycle ONLY (route untouched!)
hermes-ops-kit headroom stats                # /stats: requests, tokens saved
hermes-ops-kit headroom export               # machine-readable JSON
```

> **`up`/`down` ≠ `enable`/`disable`.** `up` only starts the process: with
> `desired: disabled` the proxy sits idle and Hermes stays direct. To route
> Hermes through the proxy, use `enable`.

### Runbook

```bash
# Activation
hermes-ops-kit headroom doctor
hermes-ops-kit headroom enable --dry-run     # preview (includes the proxy command line)
hermes-ops-kit headroom enable
hermes gateway restart                       # the gateway reloads the route

# Daily checks
hermes-ops-kit headroom status               # route: via headroom(8790) → <provider>
hermes-ops-kit headroom stats                # api_requests growing = traffic in the proxy
hermes-usage                                 # HEADROOM section

# Deactivation (exact restore)
hermes-ops-kit headroom disable
hermes gateway restart
```

### Switching models and providers (from inside Hermes)

| Picker action (`/model`)        | Effect                                                                                                                                                                                                                                                                |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Switch **model**, same provider | Nothing to do: the new slug passes through the proxy unchanged                                                                                                                                                                                                        |
| Switch **provider**             | The picker overwrites `model.provider` → the route is immediately **direct** on the new provider (proxy bypassed). On the next `preflight && hermes gateway restart` the overlay is re-applied over the new provider and the proxy is restarted with the new upstream |

How to tell whether Headroom is in use: `headroom status` (the `route:`
line), or `/model` in chat (current provider = `headroom`), or `headroom
stats` before/after a message (the `api_requests` counter increments).
`desired: enabled` but `route: direct` = drift → `preflight` heals it.

### Doctor checks

binary on PATH · `/readyz` · upstream resolvable · upstream key present ·
fallbacks stay direct (collision guard) · fallback chain non-empty · config
matches desired · **proxy upstream matches the primary's upstream** (drift) ·
no-coding profile · compression layering note (Hermes' native history
compression stays enabled by design; the aux `compression` LLM route stays
direct — `apply.aux_routes` is an explicit opt-in experiment).

### Troubleshooting

| Symptom                                                                       | Likely cause                                                               | Remedy                                                                                                             |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `desired: enabled` but `route: direct`                                        | Proxy unhealthy at the last reconcile, or provider switched via the picker | `hermes-ops-kit preflight && hermes gateway restart`                                                               |
| `APIConnectionError on custom` in the logs, reply still arrives               | Proxy died at runtime: Hermes degraded to the direct fallbacks (by design) | The next preflight restarts the proxy automatically                                                                |
| Picker shows `headroom (0 models)`                                            | Proxy down → its `/v1/models` is unreachable. If the proxy log shows `No module named 'fastapi'`, the `[proxy]` extra is missing | `pip install --user 'headroom-ai[proxy]'` (add `--break-system-packages` on PEP 668 distros), then `hermes-ops-kit headroom up` |
| Another provider's model picked under the `headroom` entry → errors/fallbacks | One proxy = one upstream: the foreign slug reaches the wrong endpoint      | Pick the model under its own provider, or switch the primary and re-run preflight                                  |
| Doctor: `fallbacks stay direct` failing                                       | A fallback points at `127.0.0.1:<port>`                                    | Remove the local `base_url` from the fallback in config.yaml                                                       |
| `enable`/`up` fails: "cannot resolve an OpenAI-compatible upstream"            | The primary provider has no `providers` entry with `base_url` + `api_key_env` in config.yaml | Add a `providers.<name>` block with `base_url` and `api_key_env` (see prerequisite above); providers accessed through native SDKs cannot be proxied |
| Everything broken, just go back to direct                                     | —                                                                          | `hermes-ops-kit headroom disable`; last resort: `cp ~/.hermes/config.yaml.bak.<ts>.headroom ~/.hermes/config.yaml` |

### Files and paths

| Path                                                   | Contents                                       |
| ------------------------------------------------------ | ---------------------------------------------- |
| `~/.hermes/ops-kit/headroom.yaml`                      | Desired state + knobs                          |
| `~/.hermes/ops-kit/run/headroom-<port>.{pid,meta,log}` | Pidfile, metadata (upstream/flags), daemon log |
| `~/.hermes/ops-kit/headroom_prev_route.json`           | Snapshot of the pre-enable route               |
| `~/.hermes/config.yaml.bak.<ts>.headroom`              | Timestamped backups of every config write      |
| `config/headroom.yaml` (in the plugin)                 | Bundled defaults (seed for the user file)      |

## Explicit limits

- **No multi-provider** behind a single proxy (`upstream.mode: litellm` is
  reserved, unmanaged).
- **Aux routes** (vision, web, compression, …) stay direct: the proxy covers
  the primary route only; `apply.aux_routes` is opt-in and only accepts
  routes whose provider matches the upstream.
- **In-chat MCP tools** (`headroom_compress`/`retrieve`/`stats`) are not part
  of `enable`; they require registering the server in `mcp_servers` and
  passing `hermes-ops-kit mcp audit` like any other MCP server.
- **No supervisor** (systemd/launchd): idempotent `up` + pre-boot
  reconciliation are enough; if the proxy dies between two preflights, the
  direct fallbacks keep Hermes operational.
