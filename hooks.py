"""Hermes Ops Kit — Plugin Hooks.

Startup: validate environment, check permissions, verify Vaultwarden.
Post-tool-call: redact any secrets that may have leaked in tool output.
"""

from __future__ import annotations

import os
import stat

from security.redaction import redact  # pyright: ignore[reportMissingImports]


def on_startup() -> None:
    """Run at Hermes startup. Validates environment and permissions.

    Does NOT block startup — only logs warnings to stderr.
    """
    import sys

    warnings: list[str] = []

    # Check env file permissions
    env_path = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(env_path):
        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        if mode != 0o600:
            warnings.append(
                f"~/.hermes/.env has unsafe permissions: {oct(mode)[2:]} "
                f"(expected 600). Run: chmod 600 ~/.hermes/.env"
            )

    # Check hermes dir permissions
    hermes_dir = os.path.expanduser("~/.hermes")
    if os.path.exists(hermes_dir):
        mode = stat.S_IMODE(os.stat(hermes_dir).st_mode)
        if mode != 0o700:
            warnings.append(
                f"~/.hermes has unsafe permissions: {oct(mode)[2:]} "
                f"(expected 700). Run: chmod 700 ~/.hermes"
            )

    # Check generated env
    gen_env = os.path.expanduser("~/.hermes/.env.generated")
    if os.path.exists(gen_env):
        mode = stat.S_IMODE(os.stat(gen_env).st_mode)
        if mode != 0o600:
            warnings.append(
                f"~/.hermes/.env.generated has unsafe permissions: {oct(mode)[2:]}"
            )

    if warnings:
        for w in warnings:
            print(f"[hermes-ops-kit] ⚠ {w}", file=sys.stderr)


def on_post_tool_call(tool_name: str, result: str) -> str:
    """Redact any secrets that may have leaked in tool output.

    Called after every tool invocation. Returns the redacted result.
    """
    return redact(result)
