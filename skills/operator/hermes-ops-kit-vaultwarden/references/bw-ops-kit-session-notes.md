# bw + Hermes Ops Kit session notes

## What worked

- `hermes-ops-kit` exposes the Vaultwarden backend through `hermes-key-rotate`, but item-level secret CRUD is often easier with `bw` directly.
- A login item can be created with `bw create item` using JSON encoded by `bw encode`.
- Verification with `bw list items --search <name>` or `bw get item <id>` is reliable.

## Example login item

```json
{
  "type": 1,
  "name": "example.com",
  "favorite": false,
  "reprompt": 0,
  "notes": "Added from image",
  "fields": [],
  "passwordHistory": [],
  "login": {
    "username": "alice",
    "password": "secret",
    "uris": [{"uri": "https://example.com", "match": null}],
    "fido2Credentials": []
  }
}
```

## Visibility gotchas

- `collectionIds: []` means the item is stored in the personal vault, not a collection.
- `bw list collections` may return `[]`; don't assume a collection exists.
- If another device doesn't show the new item, run `bw sync` and make sure it is using the same server/account.

## Session-specific observation

The user asked to save an account from an image; the item was created successfully with `bw` and then verified by searching the vault.
