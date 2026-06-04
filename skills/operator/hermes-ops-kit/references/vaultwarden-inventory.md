# Vaultwarden password inventory workflow

This note captures the session-proven workflow for producing a password inventory from the Hermes Ops Kit Vaultwarden backend.

## Goal

List only login items that actually contain a password, then summarize them by item name and username without exposing the secret value.

## Verified workflow

1. Load Vaultwarden auth from `.hermes/.env` in a way that does not blindly `source` the file.
   - `.hermes/.env` may contain shell-unfriendly lines such as `export ...` or comments.
   - Prefer reading only the needed variables, or exporting the specific variable names explicitly.
2. Unlock the vault non-interactively:
   - `bw unlock --passwordenv VAULTWARDEN_PASSWORD --raw`
3. Export the returned session key into `BW_SESSION`.
4. List items and filter to login items with a non-empty `login.password` field.
5. Print only metadata:
   - item name
   - username
   - optional folderId
   - URI if needed

## Safe Python probe pattern

Use a small Python wrapper to avoid leaking secrets and to avoid shell parsing pitfalls:

```python
from pathlib import Path
import os, subprocess, json, sys, logging

logging.basicConfig(level=logging.INFO)

# Parse only the variables you need from ~/.hermes/.env.
# Do not `source` the file wholesale; read lines safely and extract key=value.
env = os.environ.copy()
env_path = Path.home() / ".hermes" / ".env"
if env_path.exists():
   try:
      with env_path.open() as f:
         for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
               continue
            if line.startswith("export "):
               line = line[len("export ") :].strip()
            if "=" not in line:
               continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
               v = v[1:-1]
            if k == "VAULTWARDEN_PASSWORD":
               env["VAULTWARDEN_PASSWORD"] = v
               break
   except Exception as e:
      logging.error("Failed to read %s: %s", env_path, e)
      sys.exit(1)

if "VAULTWARDEN_PASSWORD" not in env:
   logging.error("VAULTWARDEN_PASSWORD not found in %s; cannot unlock vault non-interactively.", env_path)
   sys.exit(1)

# Unlock the vault and set BW_SESSION; catch errors and abort cleanly.
try:
   session = subprocess.check_output(
      ["bw", "unlock", "--passwordenv", "VAULTWARDEN_PASSWORD", "--raw"],
      text=True,
      env=env,
   ).strip()
except FileNotFoundError as e:
   logging.error("Bitwarden CLI 'bw' not found: %s", e)
   sys.exit(1)
except subprocess.CalledProcessError as e:
   logging.error("'bw unlock' failed (exit %s): %s", getattr(e, "returncode", None), getattr(e, "output", None))
   sys.exit(1)

env["BW_SESSION"] = session

# List items and parse JSON; handle CLI and parsing failures.
try:
   items_out = subprocess.check_output(["bw", "list", "items"], text=True, env=env)
   items = json.loads(items_out)
except FileNotFoundError as e:
   logging.error("Bitwarden CLI 'bw' not found: %s", e)
   sys.exit(1)
except subprocess.CalledProcessError as e:
   logging.error("'bw list items' failed (exit %s): %s", getattr(e, "returncode", None), getattr(e, "output", None))
   sys.exit(1)
except json.JSONDecodeError as e:
   logging.error("Failed to parse JSON from 'bw list items': %s", e)
   logging.debug("Output was: %s", items_out)
   sys.exit(1)

password_items = [it for it in items if (it.get('login') or {}).get('password')]
```

## Pitfalls

- Do not print the password field or the raw session token.
- Do not assume `.hermes/.env` is shell-safe.
- If there are no password-bearing login items, the correct inventory is an empty list, not an error.

## Related support files

- `references/vaultwarden-item-workflow.md` — add/update recipes for creating login items.
