"""Deprecated shim — use hermes_ops_kit.env.loader instead.

Kept only so external callers of the old names keep working; the kit
itself imports from env.loader.
"""

from __future__ import annotations

import os

from hermes_ops_kit import ops_config_io
from .loader import REQUIRED_BOOTSTRAP_VARS, validate_bootstrap  # noqa: F401


def load_dotenv(path: str, *, set_environ: bool = False) -> dict[str, str]:
    """Parse a .env file into a dict (see env.loader.parse_env_file)."""
    from .loader import parse_env_file

    result = parse_env_file(path)
    if set_environ:
        os.environ.update(result)
    return result


def get_hermes_env_path() -> str:
    return os.path.join(ops_config_io.HERMES_HOME, ".env")


def get_generated_env_path() -> str:
    return os.path.join(ops_config_io.HERMES_HOME, ".env.generated")


def load_hermes_env() -> dict[str, str]:
    """Load .env + .env.generated into os.environ; validate bootstrap vars."""
    from .loader import parse_env_file

    env = parse_env_file(get_hermes_env_path())
    gen = parse_env_file(get_generated_env_path())
    merged = {**env, **gen}
    os.environ.update(merged)
    missing = validate_bootstrap(env)
    if missing:
        import sys

        print(
            f"WARNING: missing required bootstrap vars in .env: {', '.join(missing)}",
            file=sys.stderr,
        )
    return merged
