---
name: hermes-ops-kit-vaultwarden
description: "Use Hermes Ops Kit with Vaultwarden/Bitwarden secret storage, preferring the Bitwarden CLI (`bw`) for item-level CRUD and verification."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, ops-kit, vaultwarden, bitwarden, bw, secrets, secret-store, cli]
---

# Hermes Ops Kit + Vaultwarden/Bitwarden

Use this skill when working with the `hermes-ops-kit` plugin's secret store, provider credentials, or Vaultwarden-backed items.

## Core idea

For item-level secret work, prefer the Bitwarden CLI (`bw`) directly when it is already authenticated and pointed at the right server.

Use `hermes-key-rotate` for higher-level workflows like:

- backend health checks
- secret ref listing
- env projection rendering
- provider key rotation and diagnostics

Use `bw` directly for:

- creating or updating a vault item
- inspecting an existing item
- listing items/collections/organizations
- moving an item into an organization/collection
- verifying sync visibility

## Recommended flow

1. Confirm the active backend and session.
   - `bw status`
   - `bw sync`
2. Inspect before writing.
   - `bw list items --search <name>`
   - `bw list collections`
   - `bw list organizations`
3. Create or update the item with the minimal object shape needed.
   - For login items, store `username`, `password`, and `uris` in the `login` object.
4. Verify with `bw get item <id>` or `bw list items --search <name>`.
5. Sync again if the user expects to see the change on another device.
   - `bw sync`

## Item shape notes

- A login item can be created with `type: 1`.
- If the item is meant to be a normal account, use a `login` object with:
  - `username`
  - `password`
  - `uris` (if applicable)
- If `collectionIds` is empty, the item lives in the user's personal vault (`My Vault`), not in a collection.
- An empty `bw list collections` means there may be no accessible organization/collection in the current vault session, even if the vault itself is valid.

## Moving items

If the user asks for a specific collection:

- first list organizations and collections
- identify the correct organization/collection IDs
- use Bitwarden's item move/share flow only once the IDs are known

Do not guess collection IDs.

## Session handling

If the CLI asks for a master password or appears to be in interactive mode, prefer a non-interactive session path first:

- reuse the existing `BW_SESSION` when available
- if needed, unlock once and pass `--session` explicitly to subsequent commands
- avoid depending on prompts when the task can be done deterministically

## Verification

Always verify the result with a real command.

Good checks:

- `bw get item <id>`
- `bw list items --search <name>`
- `bw status`
- `bw sync`

## Pitfalls

1. **Assuming a collection exists.** `bw list collections` can be empty; verify the organization/collection first.
2. **Confusing My Vault with a collection.** `collectionIds: []` means personal vault storage.
3. **Skipping sync.** Changes may not appear on another device until you run `bw sync`.
4. **Using the high-level rotator when you need CRUD.** `hermes-key-rotate` is for rotation and diagnostics; `bw` is usually better for item management.
5. **Guessing secret names.** Check the plugin's internal refs and item names before creating duplicates.

## Session notes

See `references/bw-ops-kit-session-notes.md` for a compact example of creating a login item with `bw`, verifying it, and the vault/collection visibility gotchas discovered in this session.
