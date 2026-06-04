"""
Hermes Ops Kit — Environment Loader

Loads ~/.hermes/.env into a dict, validates required bootstrap variables.
Never logs raw values.  Used by hermes_key_rotate.py at startup.

Required bootstrap variables (spec section 6):
  HERMES_SECRET_BACKEND
  VAULTWARDEN_SERVER_URL

Optional (depending on auth mode):
  VAULTWARDEN_USER, VAULTWARDEN_PASSWORD
  BW_CLIENTID, BW_CLIENTSECRET
  BW_SESSION
"""

from __future__ import annotations

import os


def load_dotenv(path: str, *, set_environ: bool = False) -> dict[str, str]:
    """Parse a .env-style file into a dict.  Strips quotes and whitespace.

    If *set_environ* is True, also sets os.environ for each variable.
    """
    result: dict[str, str] = {}
    if not os.path.exists(path):
        return result

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            result[key] = value
            if set_environ:
                os.environ[key] = value
    return result


REQUIRED_BOOTSTRAP_VARS = [
    "HERMES_SECRET_BACKEND",
    "VAULTWARDEN_SERVER_URL",
]


def validate_bootstrap(env: dict[str, str]) -> list[str]:
    """Return a list of missing required variables.  Empty list = valid."""
    missing: list[str] = []
    for var in REQUIRED_BOOTSTRAP_VARS:
        if not env.get(var):
            missing.append(var)
    return missing


def get_hermes_env_path() -> str:
    """Return the path to ~/.hermes/.env."""
    return os.path.expanduser("~/.hermes/.env")


def get_generated_env_path() -> str:
    """Return the path to ~/.hermes/.env.generated."""
    return os.path.expanduser("~/.hermes/.env.generated")


def load_hermes_env() -> dict[str, str]:
    """Load ~/.hermes/.env and validate bootstrap vars.

    Returns the parsed dict.  Prints warnings to stderr for missing vars.
    """
    path = get_hermes_env_path()
    env = load_dotenv(path, set_environ=True)
    missing = validate_bootstrap(env)
    if missing:
        import sys

        print(
            f"WARNING: ~/.hermes/.env missing required vars: {missing}",
            file=sys.stderr,
        )
    return env
