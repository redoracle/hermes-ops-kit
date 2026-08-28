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
