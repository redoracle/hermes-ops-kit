"""Hermes Ops Kit — Centralised Environment Loader.

Provides a single load_dotenv() entrypoint so consumers don't need to
reach into image_routes internals just to load ~/.hermes/.env.
"""

from __future__ import annotations

import os
import threading

from dotenv import load_dotenv as _dotenv_load
from hermes_ops_kit import ops_config_io  # noqa: E402

_ENV_LOADED = False
_ENV_LOCK = threading.Lock()


def load_dotenv() -> None:
    """Load environment variables from .env and .env.generated.

    Loads both files: .env first (bootstrap vars), then .env.generated
    (rendered API keys) on top.  When a key appears in both files the
    generated value wins — but vars set only in .env are preserved.

    Thread-safe: only loads once per process; safe to call repeatedly.
    """
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    with _ENV_LOCK:
        if _ENV_LOADED:
            return
        hermes_home = ops_config_io.HERMES_HOME
        # Load .env first, then .env.generated on top (override=False
        # means first-loaded wins, so generated goes second to take
        # precedence for shared keys).
        for filename in (".env", ".env.generated"):
            env_path = os.path.join(hermes_home, filename)
            if os.path.isfile(env_path):
                _dotenv_load(env_path, override=True)
        _ENV_LOADED = True


REQUIRED_BOOTSTRAP_VARS = [
    "HERMES_SECRET_BACKEND",
    "VAULTWARDEN_SERVER_URL",
]


def _parse_line(line: str) -> tuple[str, str] | None:
    """Parse one .env line → (key, value); inline # comments honored."""
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if "#" in line:
        for i, c in enumerate(line):
            if c in ('"', "'"):
                break
            if c == "#":
                line = line[:i].strip()
                break
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    if not key:
        return None
    return key, value.strip().strip('"').strip("'")


def parse_env_file(path: str) -> dict[str, str]:
    """Parse a .env-style file into a dict. Missing file → {}."""
    result: dict[str, str] = {}
    if not os.path.exists(path):
        return result
    with open(path) as f:
        for line in f:
            kv = _parse_line(line)
            if kv:
                result[kv[0]] = kv[1]
    return result


def load_env_dict(hermes_home: str | None = None) -> dict[str, str]:
    """Parse .env then .env.generated into a dict (generated wins on overlap).

    Same files, precedence and comment semantics as load_dotenv().
    """
    home = hermes_home or ops_config_io.HERMES_HOME
    result: dict[str, str] = {}
    for filename in (".env", ".env.generated"):
        result.update(parse_env_file(os.path.join(home, filename)))
    return result


def load_env_setdefault(path: str | None = None) -> None:
    """Load env vars into os.environ WITHOUT clobbering real env vars.

    Same files/precedence as load_dotenv(), but os.environ.setdefault
    semantics: variables already present in the process environment win.
    A single explicit *path* loads just that file.
    """
    if path is not None:
        files = [path]
    else:
        home = ops_config_io.HERMES_HOME
        files = [os.path.join(home, f) for f in (".env", ".env.generated")]
    # Merge all files first (later files — generated — win on overlap), THEN
    # setdefault once per key: per-file setdefault would let a .env value
    # block its .env.generated override.
    values: dict[str, str] = {}
    for fp in files:
        values.update(parse_env_file(fp))
    for key, value in values.items():
        os.environ.setdefault(key, value)


def validate_bootstrap(env: dict[str, str]) -> list[str]:
    """Return missing required bootstrap variables. Empty list = valid."""
    return [var for var in REQUIRED_BOOTSTRAP_VARS if not env.get(var)]
