# Hermes Ops Kit

Operational and security workflows for provider routing, secret and key lifecycle, preflight
plugin scanning, MCP auditing, cost governance, diagnostics, and remote assistant delegation.

## First-run setup

Every tool here resolves its secrets through the Vaultwarden backend, bootstrapped from
`~/.hermes/.env`. **Before the first run, verify these keys exist. If any is missing, do NOT
hardcode a value — ask the user for it in conversation, write it to `~/.hermes/.env`, then
`chmod 600` the file.**

| Key                      | Description                                     |
| ------------------------ | ----------------------------------------------- |
| `HERMES_SECRET_BACKEND`  | Secret backend selector — set to `vaultwarden`  |
| `VAULTWARDEN_SERVER_URL` | Vaultwarden/Bitwarden server URL (ask the user) |
| `VAULTWARDEN_USER`       | Vault account email                             |
| `VAULTWARDEN_PASSWORD`   | Master password                                 |

Show which keys are already present (never prints secret values):

```bash
for k in HERMES_SECRET_BACKEND VAULTWARDEN_SERVER_URL VAULTWARDEN_USER VAULTWARDEN_PASSWORD; do
  grep -q "^${k}=" ~/.hermes/.env 2>/dev/null && echo "$k: set" || echo "$k: MISSING — ask the user"
done
```

> [!warning] Automated / non-interactive context: never run a blocking `read` prompt — it
> hangs or stores empty credentials. Ask the user in conversation, then persist.

For a human at an interactive terminal, this idempotent helper prompts only for the keys
that are still missing (the password is read silently):

```bash
# Run with bash, interactively. Adds only the keys not already present in ~/.hermes/.env.
env=~/.hermes/.env
mkdir -p ~/.hermes && touch "$env" && chmod 600 "$env"
grep -q '^HERMES_SECRET_BACKEND=' "$env" || printf 'HERMES_SECRET_BACKEND=%s\n' vaultwarden >> "$env"
grep -q '^VAULTWARDEN_SERVER_URL=' "$env" || { read -rp  'Vaultwarden URL: '     v; printf 'VAULTWARDEN_SERVER_URL=%s\n' "$v" >> "$env"; }
grep -q '^VAULTWARDEN_USER='       "$env" || { read -rp  'Vault account email: ' v; printf 'VAULTWARDEN_USER=%s\n'       "$v" >> "$env"; }
grep -q '^VAULTWARDEN_PASSWORD='   "$env" || { read -rsp 'Vault master password: ' v; echo; printf 'VAULTWARDEN_PASSWORD=%s\n' "$v" >> "$env"; }
chmod 600 "$env"
```

Once the bootstrap exists, verify the backend before using any tool:

```bash
hermes-key-rotate --doctor-secrets
```

## Tools

| Tool                       | Description                                                         |
| -------------------------- | ------------------------------------------------------------------- |
| `ai_provider_invoke`       | Invoke an AI provider (OpenAI, Anthropic, Gemini, DeepSeek, GitHub) |
| `ai_assistant_delegate`    | Delegate a read-only task to a remote Hermes assistant              |
| `ai_usage_metrics`         | Usage, health, rate limits, costs across all providers + assistants |
| `ai_key_rotate`            | Safe key rotation (dry-run, status, doctor, apply)                  |
| `ai_secret_backend_status` | Vaultwarden backend health (never exposes secrets)                  |

## CLI Commands

```bash
hermes-ops-kit status                     # Health overview
hermes-ops-kit usage --compact            # Usage metrics
hermes-ops-kit rotate --dry-run           # Key rotation preview
hermes-ops-kit doctor                     # Full diagnostic
hermes-ops-kit assistants list            # List remote assistants
hermes-ops-kit assistants ping assistant-id      # Ping Assistant Profiler
hermes-ops-kit install doctor             # Install checks
```

## Standalone Binaries

```bash
hermes-ops-kit status
hermes-usage --compact
hermes-key-rotate --doctor-secrets
```

## Architecture

```
PROVIDERS   = external LLM providers (OpenAI, Anthropic, Gemini, DeepSeek, GitHub)
ASSISTANTS  = remote Hermes agents (see config/assistants.yaml)
SECURITY    = Vaultwarden backend + redaction + policy engine
ENV         = ~/.hermes/.env.generated (rendered from Vaultwarden)
```

## Configuration

- `config/assistants.yaml` — remote assistant registry
- `config/env_projection.yaml` — env var → secret ref mappings
- `~/.hermes/.env` — Vaultwarden bootstrap credentials
- `~/.hermes/.env.generated` — runtime env (rendered from Vaultwarden)

## Security

- No raw secrets in stdout, stderr, logs, audit, or Obsidian
- Secret scanner gate before every Obsidian/audit write
- Two-phase key rotation (never revoke before smoke test)
- Atomic env writes (temp → chmod 600 → fsync → rename)
- HTTPS required for secret backend
- Read-only by default for remote assistants
