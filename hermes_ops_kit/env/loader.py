"""Hermes Ops Kit — Centralised Environment Loader.

Provides a single load_dotenv() entrypoint so consumers don't need to
reach into image_routes internals just to load ~/.hermes/.env.

Every loader here parses files with python-dotenv and ``interpolate=False``:
``${VAR}`` is never expanded, because expansion would read the ambient
environment (possibly another profile's credentials) into parsed values.
"""

from __future__ import annotations

import os
import threading

from dotenv import dotenv_values as _dotenv_values
from dotenv import load_dotenv as _dotenv_load
from .. import ops_config_io  # noqa: E402

_ENV_LOADED = False
_ENV_LOCK = threading.Lock()


def load_dotenv() -> None:
    """Load environment variables from .env and .env.generated.

    Loads both files: .env first (bootstrap vars), then .env.generated
    (rendered API keys) on top.  When a key appears in both files the
    generated value wins — but vars set only in .env are preserved.

    Thread-safe: only loads once per process; safe to call repeatedly.

    Inside Hermes (embedded) this does nothing: the process environment is shared
    by every profile, so writing one profile's keys into it would leak them to the
    others. Embedded code reads credentials with ``get_secret()`` instead.
    """
    global _ENV_LOADED
    if ops_config_io.core_loaded():
        return
    if _ENV_LOADED:
        return
    with _ENV_LOCK:
        if _ENV_LOADED:
            return
        hermes_home = ops_config_io.home()
        # Load .env first, then .env.generated on top (override=False
        # means first-loaded wins, so generated goes second to take
        # precedence for shared keys).
        for filename in (".env", ".env.generated"):
            env_path = os.path.join(hermes_home, filename)
            if os.path.isfile(env_path):
                _dotenv_load(env_path, override=True, interpolate=False)
        _ENV_LOADED = True


REQUIRED_BOOTSTRAP_VARS = [
    "HERMES_SECRET_BACKEND",
    "VAULTWARDEN_SERVER_URL",
]


def parse_env_file(path: str) -> dict[str, str]:
    """Parse a .env-style file into a dict. Missing file → {}.

    python-dotenv syntax: optional ``export``, quoted values with escapes,
    multiline quoted values, ``#`` comments after whitespace. Interpolation is
    off; a line without ``=`` or a malformed line yields no key.
    """
    if not os.path.exists(path):
        return {}
    values = _dotenv_values(path, interpolate=False, encoding="utf-8")
    return {key: value for key, value in values.items() if value is not None}


def load_env_dict(hermes_home: str | None = None) -> dict[str, str]:
    """Parse .env then .env.generated into a dict (generated wins on overlap).

    Same files, precedence and comment semantics as load_dotenv().
    """
    home = hermes_home or ops_config_io.home()
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
        home = ops_config_io.home()
        files = [os.path.join(home, f) for f in (".env", ".env.generated")]
    # Merge all files first (later files — generated — win on overlap), THEN
    # setdefault once per key: per-file setdefault would let a .env value
    # block its .env.generated override.
    if ops_config_io.core_loaded():
        return  # embedded: never write the shared process environment (see load_dotenv)
    values: dict[str, str] = {}
    for fp in files:
        values.update(parse_env_file(fp))
    for key, value in values.items():
        os.environ.setdefault(key, value)


def get_secret(name: str, default: str | None = None) -> str | None:
    """Resolve a credential for the active profile without touching ``os.environ``.

    Order: the profile's ``.env``/``.env.generated`` values (generated wins, same
    as ``load_dotenv``), then core's scoped ``get_secret`` when embedded in
    Hermes, else the real process environment. Embedded, a read made with no
    profile scope bound while Hermes multiplexes profiles raises core's
    ``UnscopedSecretError`` before any file is read, so one profile's key can
    never answer for another. Callers must let that error propagate.
    """
    embedded = ops_config_io.core_loaded()
    if embedded:
        current_scope = ops_config_io.native(
            "agent.secret_scope", "current_secret_scope"
        )
        multiplexing = ops_config_io.native("agent.secret_scope", "is_multiplex_active")
        if current_scope() is None and multiplexing():
            unscoped = ops_config_io.native("agent.secret_scope", "UnscopedSecretError")
            raise unscoped(
                name,
                f"ops-kit get_secret({name!r}) called with no profile secret scope "
                "bound while multiplexing is on",
            )
    values = load_env_dict()
    if values.get(name):  # an empty entry (``KEY=``) is "unset", not an override
        return values[name]
    if embedded:
        scoped_get = ops_config_io.native("agent.secret_scope", "get_secret")
        return scoped_get(name, default)
    return os.environ.get(name, default)


def validate_bootstrap(env: dict[str, str]) -> list[str]:
    """Return missing required bootstrap variables. Empty list = valid."""
    return [var for var in REQUIRED_BOOTSTRAP_VARS if not env.get(var)]
