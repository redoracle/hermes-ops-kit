---
name: hermes-ops-kit
description: Hermes Ops Kit plugin workflows for secret storage, key rotation, usage metrics, and operator diagnostics.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, plugin, vaultwarden, bitwarden, secrets, key-rotation, diagnostics]
---

# Hermes Ops Kit

Use this skill when working with the `hermes-ops-kit` plugin: checking status, inspecting its secret store, rotating provider keys, rendering env projections, or creating new Vaultwarden items that the plugin can later consume.

## What this plugin is for

Hermes Ops Kit is the bridge between Hermes and a Vaultwarden/Bitwarden-backed secret store. In this setup it provides:

- secret backend health checks
- key rotation orchestration
- secret listing / env rendering
- usage metrics and assistant routing helpers

It does *not* currently expose a dedicated generic `add-secret` subcommand. When you need to store a new credential for Hermes Ops Kit, use Bitwarden CLI item creation directly and verify the item afterwards.

## Typical workflow

1. Inspect plugin availability:
   - `hermes plugins list`
   - `hermes-ops-kit doctor`
   - `hermes-key-rotate --secret-backend vaultwarden --healthcheck`
2. Inspect known Hermes refs:
   - `hermes-key-rotate --secret-backend vaultwarden --list-refs`
3. Render env projection if needed:
   - `hermes-key-rotate --render-env`
4. Unlock the Bitwarden vault non-interactively when `.hermes/.env` provides the auth variables:
   - `bw unlock --passwordenv VAULTWARDEN_PASSWORD --raw`
   - export the returned session key into `BW_SESSION`
5. Inventory password-bearing items:
   - `bw list items`
   - filter to entries where `login.password` is present
   - print only item name, username, folderId, and optional URI metadata
6. Create a new secret item when no plugin command exists for it:
   - `bw encode < item.json`
   - `bw create item <encoded-json>`
7. Verify the item by name:
   - `bw list items --search <item-name>`
   - `bw get item <id>` if you need the full object

### Pitfall: avoid trying to inventory while locked

If `bw status` reports `locked`, inventory commands may fail or prompt for the master password. Unlock first, then run listing/search commands.

## Item naming and storage conventions

- Hermes-managed items use Vaultwarden names like `Hermes/<Group>/<KEY>`.
- Store credentials you want Hermes to consume as Bitwarden login items when possible.
- Keep the human-readable item name stable; Hermes secret refs are mapped from the item name.
- Use explicit names instead of searching by username; Bitwarden search is name-centric and a username match may not return the item.

## Important pitfall: no generic Hermes secret-create command

A common mistake is to look for a plugin command that writes arbitrary credentials into Vaultwarden. In this plugin, the supported paths are mostly read/diagnostic/rotation. For one-off credential storage, create a Bitwarden item directly and verify it.

Auth note for this setup:

- `.hermes/.env` is the source of truth for Vaultwarden access variables
- expected variables include `VAULTWARDEN_SERVER_URL`, `VAULTWARDEN_USER`, `VAULTWARDEN_PASSWORD`, `BW_SESSION`, and `HERMES_AUTH_MODE=bitwarden_cli_session`
- do not hardcode these values into skills; reference the environment variables only
- do not blindly `source` `.hermes/.env`; parse or export only the needed keys because the file may include shell-unfriendly lines

Recommended login-item JSON shape:

```json
{
  "type": 1,
  "name": "Hermes/Zeus/LOGIN",
  "favorite": false,
  "notes": "Added via Hermes ops-kit",
  "login": {
    "username": "<username>",
    "password": "<secret>",
    "uris": []
  },
  "fields": [],
  "passwordHistory": []
}
```

## Verification checklist

- `hermes-key-rotate --secret-backend vaultwarden --healthcheck` reports authenticated + unlocked
- `hermes-key-rotate --secret-backend vaultwarden --list-refs` shows Hermes refs when they exist
- Newly created Bitwarden items can be found with `bw list items --search <item-name>`
- The stored username/password item is a login item, not a plain note, when the credential should be reusable by a user or service

## Related support files

- `references/vaultwarden-item-workflow.md` — session-derived create/verify recipe and pitfalls
