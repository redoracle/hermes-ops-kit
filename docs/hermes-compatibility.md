# Hermes Compatibility

Hermes Ops Kit is an **optional operational/security plugin** for the
[Hermes Agent](https://github.com/NousResearch/hermes-agent). It extends
Hermes — it does NOT replace any core functionality.

## What Ops Kit Provides

| Capability           | How it works                                                    |
|----------------------|-----------------------------------------------------------------|
| Secret projection    | Reads from Vaultwarden, renders `~/.hermes/.env.generated`      |
| Key rotation         | Two-phase rotation (store → smoke → activate → revoke)         |
| Provider health      | Live API probes for all 5 LLM providers                        |
| Route diagnostics    | Reads `~/.hermes/config.yaml` model/aux/fallback, displays in UI |
| MCP auditing         | Scans configured MCP servers, classifies tools by risk          |
| Usage/cost telemetry | OpenAI + Anthropic admin API cost data (needs admin keys)       |
| Config patching      | Writes `~/.hermes/config.yaml` for route profiles, image_gen    |

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
    - hermes-ops-kit        # Ops kit plugin
    - ops-kit-router        # Image generation bridge

image_gen:
  provider: ops-kit-router
  model: auto
```

## Files Written by Ops Kit

| Path | Purpose | Safe to delete? |
|------|---------|-----------------|
| `~/.hermes/.env.generated` | Rendered env vars from Vaultwarden | Yes (re-render with `hermes-key-rotate --render-env`) |
| `~/.hermes/ops-kit/routes.yaml` | Route display labels + profiles | Yes (will be regenerated from defaults) |
| `~/.hermes/ops-kit/image_routes.yaml` | Image generation routes | Yes (will be regenerated) |
| `~/.hermes/ops-kit/assistants.yaml` | Assistant registry | **No** (your assistant configuration) |
| `~/.hermes/ops-kit/budget.yaml` | Budget/cost limits | Yes (will be regenerated) |
| `~/.hermes/ops-kit/audit/events.jsonl` | Audit trail | Yes (but you lose audit history) |
| `~/.hermes/key-rotation-audit.jsonl` | Key rotation log | Yes (but you lose rotation history) |
| `~/.hermes/assistants/audit.jsonl` | Assistant delegation log | Yes (but you lose delegation history) |
| `~/.hermes/assistants/tasks.sqlite` | Task lifecycle DB | Yes (active tasks lost) |
| `~/.hermes/mcp_policy.json` | MCP tool approval whitelist | Yes (will be regenerated) |

## Environment Variables

Ops Kit requires these in `~/.hermes/.env` (chmod 600):

```bash
HERMES_SECRET_BACKEND=vaultwarden          # Currently only Vaultwarden is supported
VAULTWARDEN_SERVER_URL=https://<host>       # Your Vaultwarden/Bitwarden server
VAULTWARDEN_USER=<email>                    # Bootstrap user
VAULTWARDEN_PASSWORD=<master-password>      # Bootstrap password
HERMES_AUTH_MODE=bitwarden_cli_session      # or bitwarden_cli_password, bitwarden_cli_api_key
```

## Rollback

Every mutating operation preserves the previous state:

- **Key rotation:** `backup_secret()` before rotation → `restore_secret()` on failure
- **Config patches:** `~/.hermes/config.yaml.bak` created before mutations
- **Env rendering:** atomic write (temp → fsync → rename) — never partial files

## Version Compatibility

| Ops Kit | Hermes Agent |
|---------|-------------|
| 0.2.x   | 0.15.x       |

Ops Kit is developed against the Hermes plugin API. Breaking changes in the
Hermes plugin system may require ops-kit updates.
