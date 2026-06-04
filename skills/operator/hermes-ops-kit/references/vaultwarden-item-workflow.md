# Vaultwarden item workflow for Hermes Ops Kit

This note captures a session-derived recipe for storing a one-off credential in the Hermes Ops Kit Vaultwarden backend.

## When to use

Use this when the user asks to add a credential to Hermes Ops Kit but the plugin only exposes diagnostics, rotation, and listing commands.

## Auth bootstrap on this setup

- `.hermes/.env` provides Vaultwarden auth and server settings
- use `bw unlock --passwordenv VAULTWARDEN_PASSWORD --raw` to obtain a session key when the vault is locked
- export the returned value to `BW_SESSION` before running item commands
- `HERMES_AUTH_MODE=bitwarden_cli_session` indicates the Bitwarden CLI session is the active auth mode

## Verified session recipe

1. Check plugin state:
   - `hermes plugins list`
   - confirm `hermes-ops-kit` is enabled
2. Check secret backend health:
   - `hermes-key-rotate --secret-backend vaultwarden --healthcheck`
3. List known Hermes refs:
   - `hermes-key-rotate --secret-backend vaultwarden --list-refs`
4. Create a Bitwarden login item directly when no plugin write-command exists.
   - Use `bw encode` with a JSON item body
   - Use `bw create item <encoded-json>`
5. Verify by searching the created item name:
   - `bw list items --search <item-name>`
   - If needed, fetch full item details with `bw get item <id>`

## Verified item shape

A login item worked with this general structure:

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

## Pitfalls

- Searching by username alone may return nothing; search by the item name instead.
- Hermes Ops Kit secret refs are name-mapped, so stable item names matter.
- `hermes-key-rotate --list-refs` only shows Hermes refs, not arbitrary Bitwarden items.

## What was verified

- A Bitwarden login item could be created successfully with `bw create item`.
- The resulting item was searchable by its name.
- The created item stored the username and password in the `login` object.
