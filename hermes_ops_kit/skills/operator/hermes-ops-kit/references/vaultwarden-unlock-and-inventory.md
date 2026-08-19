# Vaultwarden unlock and inventory notes

Session-derived workflow for this Hermes setup:

- `bw status` is the fastest preflight check; if it reports `locked`, inventory commands are not useful yet.
- `bw unlock --help` confirms the supported non-interactive forms:
  - `bw unlock <password>`
  - `bw unlock --passwordenv <passwordenv>`
  - `bw unlock --passwordfile <passwordfile>`
  - `bw unlock --raw` for session key only
- For this setup, the unlock password may live in a `.env` file or be exposed as an env var before running `bw unlock --passwordenv <ENV_VAR>`.
- After unlock, use `bw status` again to confirm `status: unlocked` before attempting item inventory.
- Inventory pattern:
  - `bw list items`
  - `bw list items --search <name>` for targeted lookup
  - `bw get item <id>` for a full object when needed

Safety note:

- Do not read secret-bearing `.env` files through file-read tools when the environment blocks that path; use shell inspection or explicit env handling instead.
