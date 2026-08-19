# Bitwarden CLI (`bw`)

Manage Vaultwarden/Bitwarden vault items directly via the `bw` CLI. This is the canonical secret store for Hermes — all AI provider keys, assistant credentials, and sensitive config live here.

## First-run setup

This skill needs the Vaultwarden bootstrap in `~/.hermes/.env`. **Before running any
command below, verify these keys exist. If any is missing, do NOT hardcode a value —
ask the user for it in conversation, write it to `~/.hermes/.env`, then `chmod 600` the file.**

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

## Setup

```bash
brew install bitwarden-cli

# Read the server URL from ~/.hermes/.env (see "First-run setup").
# Use grep|cut, never `source`: a master password may contain shell metacharacters.
bw config server "$(grep '^VAULTWARDEN_SERVER_URL=' ~/.hermes/.env | cut -d= -f2-)"
```

## Authentication

```bash
# bw consumes BW_PASSWORD / BW_CLIENTID / BW_SESSION from the environment.
# Bridge the bootstrap user + master password into the vars bw expects.
# Read values with grep|cut (never `source`: a password may contain shell metacharacters).
VAULTWARDEN_USER="$(grep '^VAULTWARDEN_USER=' ~/.hermes/.env | cut -d= -f2-)"
export BW_PASSWORD="$(grep '^VAULTWARDEN_PASSWORD=' ~/.hermes/.env | cut -d= -f2-)"

# One-time login (stores session in BW_SESSION)
bw login "$VAULTWARDEN_USER" --passwordenv BW_PASSWORD

# Daily unlock (session expires periodically)
bw unlock --passwordenv BW_PASSWORD
# → copy exported BW_SESSION to ~/.hermes/.env

# API key login (preferred for automation)
bw login --apikey   # requires BW_CLIENTID + BW_CLIENTSECRET in env

# Check current status
bw status
```

## Vault Operations

```bash
bw sync                    # Pull latest from server
bw lock                    # Lock the vault
bw status                  # Show: unauthenticated / locked / unlocked
```

## Item CRUD

### List & Search

```bash
bw list items                                        # All items
bw list items --search "OpenAI"                      # Filter by name
bw list items --folderid <id>                        # Filter by folder
bw list items --collectionid <id>                    # Filter by collection
```

### Get

```bash
bw get item <item-id>                                # Full item JSON
bw get password <item-id>                            # Password field only (plain text)
bw get username <item-id>                            # Username field only (plain text)
```

### Create

```bash
# Secure note (type: 1)
bw create item '{
  "organizationId": null,
  "collectionIds": null,
  "folderId": null,
  "type": 1,
  "name": "Hermes/OpenAI/API_KEY",
  "notes": "<api-key-value>",
  "favorite": false,
  "fields": [
    {"name": "provider", "value": "openai", "type": 0},
    {"name": "fingerprint", "value": "sha256:...", "type": 0},
    {"name": "last4", "value": "abc1", "type": 0}
  ]
}'

# Login item (type: 1, with username/password)
bw create item '{
  "type": 1,
  "name": "My Service Credentials",
  "login": {
    "username": "user@example.com",
    "password": "my-password"
  }
}'
```

### Edit

```bash
bw get item <item-id> | jq '...' | bw edit item <item-id>
# JSON is passed via stdin — edit the output of `bw get item`
```

### Delete

```bash
bw delete item <item-id>                             # Permanent deletion
```

## Hermes Item Naming Convention

All Hermes-managed secrets follow the pattern `Hermes/<Provider>/<KEY_NAME>`:

| Internal Ref                             | Vaultwarden Item Name                 |
| ---------------------------------------- | ------------------------------------- |
| `hermes/openai/api_key`                  | `Hermes/OpenAI/API_KEY`               |
| `hermes/anthropic/api_key`               | `Hermes/Anthropic/API_KEY`            |
| `hermes/google/gemini_api_key`           | `Hermes/Google/GEMINI_API_KEY`        |
| `hermes/deepseek/api_key`                | `Hermes/DeepSeek/API_KEY`             |
| `hermes/github/token`                    | `Hermes/GitHub/TOKEN`                 |
| `hermes/assistants/assistant-id/api_key` | `Hermes/Assistants/Assistant/API_KEY` |

## Security Rules

> [!warning] Never pass secrets as CLI arguments — use stdin or environment variables.
> [!warning] Never set `BITWARDENCLI_APPDATA_DIR` unless you must — if you do, use an absolute path.
> [!warning] `bw serve` is restricted to `127.0.0.1` only (`0.0.0.0` is blocked).
> [!warning] Forbidden commands: `export`, `import`, `share`, `send` — blocked by the Hermes wrapper.

- `bw` CLI uses subprocess with `list` args only — never `shell=True`.
- Secrets are passed via `BW_PASSWORD` env var or stdin, never in command arguments.
- All stdout/stderr is redacted before logging via `security.redaction.redact()`.
- `~/.hermes/.env` must be `chmod 600`.
- HTTPS required for all Vaultwarden communication.

## Integration with Hermes

The Python wrapper in `security/bitwarden_cli_client.py` wraps all these commands safely. Use it for automated workflows:

```python
import os

from security.bitwarden_cli_client import BitwardenCLIClient

# Server URL comes from ~/.hermes/.env — never hardcode it.
client = BitwardenCLIClient(server_url=os.environ["VAULTWARDEN_SERVER_URL"])
client.login_session(session_key)
items = client.list_items(search="OpenAI")
client.create_item({...})
client.edit_item(item_id, {...})
client.delete_item(item_id)
```

For ad-hoc vault management, use `bw` CLI directly as documented above.

## Troubleshooting

```bash
# Session expired → re-unlock
bw unlock --passwordenv BW_PASSWORD

# Server not configured (URL comes from ~/.hermes/.env)
bw config server "$(grep '^VAULTWARDEN_SERVER_URL=' ~/.hermes/.env | cut -d= -f2-)"

# Check connectivity
bw status

# Force re-sync
bw sync

# CLI not found
brew install bitwarden-cli
```

## Operator pitfalls

Hard-won gotchas from real item-management sessions (full playbook: [`skills/operator/`](../operator/README.md)):

- **No generic "create secret" plugin command.** Hermes Ops Kit exposes read / diagnostic / rotation paths only. For a one-off credential, create the item with `bw create item` directly, then verify it.
- **Unlock before inventory.** If `bw status` reports `locked`, listing/search may fail or prompt. Run `bw unlock` first, confirm `unlocked`, then inventory.
- **Search by item name, not username.** Bitwarden search is name-centric; a username match may not return the item. Keep item names stable — Hermes secret refs map from the name.
- **`collectionIds: []` means personal vault**, not a collection. `bw list collections` can legitimately return `[]`; don't assume a collection exists or guess its ID.
- **Sync is not implicit.** Run `bw sync` if a change must appear on another device.

## Related

- `hermes-key-rotate` — automated key rotation backed by this vault
- `hermes-ops-kit` — multi-provider AI orchestration with secret backend
- [[HERMES_KEY_ROTATION_SPEC|Hermes Key Rotation Spec]]
- [[AI_PROVIDER_ROTATION_RUNBOOK|Rotation Runbook]]
