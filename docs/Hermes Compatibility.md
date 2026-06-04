---
title: Hermes Compatibility
tags: [hermes, ops-kit, integration, security]
created: 2026-06-04
modified: 2026-06-04
---

# Hermes Compatibility

Hermes Ops Kit is an **optional operational/security plugin** for the
[Hermes Agent](https://github.com/NousResearch/hermes-agent). It extends
Hermes — it does NOT replace any core functionality.

## What Ops Kit Provides

| Capability           | How it works                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------------- |
| Secret projection    | Reads from Bitwarden/Vaultwarden, renders `~/.hermes/.env.generated` (3-layer admin denylist) |
| Key rotation         | 14-phase state machine with retry, rollback, per-provider locking, orphan cleanup             |
| Key validation       | Structured `ValidationResult` with `reason_class`, `http_status`, `retry_recommended`         |
| Emergency rotation   | Immediate revoke + replace on key compromise via `emergency` subcommand                       |
| Bulk seed            | `seed-from-env` migrates all runtime keys from `.env` to Bitwarden/Vaultwarden                |
| Admin key seeding    | `seed-admin` subcommand with live API validation before storage                               |
| Provider health      | Live API probes for all 5 LLM providers                                                       |
| Route diagnostics    | Reads `~/.hermes/config.yaml` model/aux/fallback, displays in UI                              |
| MCP auditing         | Scans configured MCP servers, classifies tools by risk                                        |
| Usage/cost telemetry | OpenAI + Anthropic admin API cost data (needs admin keys)                                     |
| Config patching      | Writes `~/.hermes/config.yaml` for route profiles, image_gen                                  |

## What Ops Kit Does NOT Replace

Ops Kit never touches:

- **Hermes runtime routing** — model invocation decisions remain in Hermes core
- **Model invocation** — the actual LLM calls are Hermes core responsibility
- **Memory** — Hermes native memory system
- **Skills** — Hermes skill loader and execution
- **Scheduler** — Hermes cron/task scheduler
- **Gateway** — Hermes API gateway (health, routing, auth)
- **Auxiliary route resolution** — Hermes determines which model handles vision, web_extract, etc.

Ops Kit provides **metadata and tooling** around these systems — not replacements for them.

## Required Hermes Configuration

In `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - hermes-ops-kit # Ops kit plugin
    - ops-kit-router # Image generation bridge

image_gen:
  provider: ops-kit-router
  model: auto
```

## Files Written by Ops Kit

| Path                                      | Purpose                                      | Safe to delete?                                     |
| ----------------------------------------- | -------------------------------------------- | --------------------------------------------------- |
| `~/.hermes/.env.generated`                | Rendered env vars from Bitwarden/Vaultwarden | Yes (re-render with `hermes-key-rotate render-env`) |
| `~/.hermes/.env.generated.bak`            | Previous .env.generated before rotation      | Yes (backup is rotated on each render)              |
| `~/.hermes/locks/<provider>.lock`         | Per-provider rotation advisory lock          | Yes (auto-cleaned on process exit)                  |
| `~/.hermes/rotation_checkpoints/<p>.json` | Rotation state machine checkpoint            | Yes (only relevant during an active rotation)       |
| `~/.hermes/ops-kit/routes.yaml`           | Route display labels + profiles              | Yes (will be regenerated from defaults)             |
| `~/.hermes/ops-kit/image_routes.yaml`     | Image generation routes                      | Yes (will be regenerated)                           |
| `~/.hermes/ops-kit/assistants.yaml`       | Assistant registry                           | **No** (your assistant configuration)               |
| `~/.hermes/ops-kit/budget.yaml`           | Budget/cost limits                           | Yes (will be regenerated)                           |
| `~/.hermes/ops-kit/audit/events.jsonl`    | Audit trail                                  | Yes (but you lose audit history)                    |
| `~/.hermes/key-rotation-audit.jsonl`      | Key rotation log                             | Yes (but you lose rotation history)                 |
| `~/.hermes/assistants/audit.jsonl`        | Assistant delegation log                     | Yes (but you lose delegation history)               |
| `~/.hermes/assistants/tasks.sqlite`       | Task lifecycle DB                            | Yes (active tasks lost)                             |
| `~/.hermes/mcp_policy.json`               | MCP tool approval whitelist                  | Yes (will be regenerated)                           |

## Security Model

Ops Kit follows a **"secret zero" architecture** — the same pattern used by
HashiCorp Vault and AWS Secrets Manager:

```text
~/.hermes/.env          (4 bootstrap vars, chmod 600)
        │
        │ bw unlock (TLS 1.3 → self-hosted Bitwarden/Vaultwarden)
        ▼
Bitwarden/Vaultwarden    (API keys encrypted at rest, AES-256)
        │
        │ hermes-key-rotate render-env (3-layer admin denylist)
        ▼
~/.hermes/.env.generated (runtime keys only, chmod 600, atomic write)
        │
        │ Hermes Agent reads at startup
        ▼
     Hermes
```

**Why this is safer:** instead of 10+ long-lived API keys sitting in a
single `.env` file forever, you have 4 bootstrap vars that unlock an
encrypted store. The `BW_SESSION` is short-lived (daily expiry) and
revocable. Provider keys can be rotated without touching `.env`.

**Admin keys are stored in Bitwarden/Vaultwarden too** (`seed-admin`), classified
as `secret_class=admin` with `renderable_to_env=false`. The 3-layer
denylist ensures they can never reach `.env.generated`. This means the
auto-creation and auto-revocation features (OpenAI Admin API, Anthropic
Admin API, Google API Keys API) work without any admin credentials in
the runtime environment.

## Gemini API Key

Ops Kit standardises on `GEMINI_API_KEY` for all Google Gemini operations
(text generation, image generation, key rotation). `GOOGLE_API_KEY` is
no longer rendered by `hermes-key-rotate render-env` (removed from the
default projection mapping in `env_projection.yaml`). The Gemini image
adapter temporarily suppresses `GOOGLE_API_KEY` during client creation
to prevent SDK auto-detection warnings when both vars coexist.

If you need `GOOGLE_API_KEY` for other Google tools, add it back to your
`env_projection.yaml` — both vars map to the same `hermes/google/gemini_api_key`
Vaultwarden secret.

## Environment Variables

Ops Kit requires **only these bootstrap vars** in `~/.hermes/.env` (chmod 600).
Provider API keys should be stored in Bitwarden/Vaultwarden, not in `.env`:

```bash
HERMES_SECRET_BACKEND=vaultwarden          # Currently only Bitwarden/Vaultwarden is supported
VAULTWARDEN_SERVER_URL=https://<host>       # Your Vaultwarden/Bitwarden server
VAULTWARDEN_USER=<email>                    # Bootstrap user
VAULTWARDEN_PASSWORD=<master-password>      # Bootstrap password
HERMES_AUTH_MODE=bitwarden_cli_session      # or bitwarden_cli_password, bitwarden_cli_api_key
```

After seeding keys into Bitwarden/Vaultwarden (`hermes-key-rotate rotate --provider <p>`),
remove provider API keys from `.env`. The rendered `.env.generated` file is
the runtime source for Hermes Agent.

### Migration path

```bash
# 1. Seed keys
echo "sk-..." | hermes-key-rotate rotate --provider openai --manual-new-key-stdin
# ... repeat for all providers

# 2. Verify
hermes-key-rotate --status          # all refs present
hermes-key-rotate render-env        # generates .env.generated

# 3. Remove provider keys from ~/.hermes/.env
# Keep only: HERMES_SECRET_BACKEND, VAULTWARDEN_*, HERMES_AUTH_MODE, BW_SESSION
```

## Rollback

Every mutating operation preserves the previous state:

- **Key rotation:** `backup_secret()` before rotation → `restore_secret()` on failure
- **Config patches:** `~/.hermes/config.yaml.bak` created before mutations
- **Env rendering:** atomic write (temp → fsync → rename) — never partial files

## Version Compatibility

| Ops Kit | Hermes Agent |
| ------- | ------------ |
| 0.2.x   | 0.15.x       |

Ops Kit is developed against the Hermes plugin API. Breaking changes in the
Hermes plugin system may require ops-kit updates.

## Related

- [[Architecture]] — module architecture
- [[Threat Model]] — security threat model
- [[Key Management Lifecycle]] — full secret lifecycle, rotation modes, revocation matrix
- [[Operations Runbook]] — incident response procedures
- [[Quickstart]] — getting started guide
