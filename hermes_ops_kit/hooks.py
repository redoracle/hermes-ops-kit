"""Hermes Ops Kit — Plugin Hooks.

Session start: validate permissions and run a cached plugin security scan.
Post-tool-call: redact any secrets that may have leaked in tool output.
"""

from __future__ import annotations

import os
import stat

from .security.redaction import redact  # pyright: ignore[reportMissingImports]
from hermes_ops_kit import ops_config_io  # noqa: E402


def _permission_warnings() -> list[str]:
    """Return warnings for unsafe Hermes secret-file permissions."""
    warnings: list[str] = []

    env_path = os.path.join(ops_config_io.HERMES_HOME, ".env")
    if os.path.exists(env_path):
        mode = stat.S_IMODE(os.stat(env_path).st_mode)
        if mode != 0o600:
            warnings.append(
                f"~/.hermes/.env has unsafe permissions: {oct(mode)[2:]} "
                f"(expected 600). Run: chmod 600 ~/.hermes/.env"
            )

    # Check hermes dir permissions
    hermes_dir = ops_config_io.HERMES_HOME
    if os.path.exists(hermes_dir):
        mode = stat.S_IMODE(os.stat(hermes_dir).st_mode)
        if mode != 0o700:
            warnings.append(
                f"~/.hermes has unsafe permissions: {oct(mode)[2:]} "
                f"(expected 700). Run: chmod 700 ~/.hermes"
            )

    # Check generated env
    gen_env = os.path.join(ops_config_io.HERMES_HOME, ".env.generated")
    if os.path.exists(gen_env):
        mode = stat.S_IMODE(os.stat(gen_env).st_mode)
        if mode != 0o600:
            warnings.append(
                f"~/.hermes/.env.generated has unsafe permissions: {oct(mode)[2:]}"
            )

    return warnings


def plugin_security_scan(**_kwargs: object) -> None:
    """Report cached startup-profile scan decisions for all installed plugins.

    Hermes session hooks run after plugins are loaded, so this hook is
    intentionally report-only. Use ``hermes-ops-kit preflight`` before the
    Hermes process starts when unsafe plugins must be excluded from loading.
    """
    import sys

    try:
        from .security.plugin_scanner.enforce import (  # pyright: ignore[reportMissingImports]
            get_enforcement_decisions,
        )

        decisions = get_enforcement_decisions()
        for plugin_name in decisions["blocked"]:
            detail = decisions["details"].get(plugin_name, "blocked by security policy")
            print(
                f"[hermes-ops-kit] CRITICAL: {plugin_name}: {detail}. "
                "Run `hermes-ops-kit preflight` before the next boot.",
                file=sys.stderr,
            )
        for plugin_name in decisions["deferred"]:
            detail = decisions["details"].get(plugin_name, "requires approval")
            print(
                f"[hermes-ops-kit] WARNING: {plugin_name}: {detail}. "
                "Run `hermes-ops-kit plugin policy` to review.",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"[hermes-ops-kit] WARNING: session-start plugin security scan failed: {exc}",
            file=sys.stderr,
        )


def on_session_start(**kwargs: object) -> None:
    """Run non-blocking permission and plugin security checks."""
    import sys

    warnings = _permission_warnings()
    if warnings:
        for w in warnings:
            print(f"[hermes-ops-kit] ⚠ {w}", file=sys.stderr)

    plugin_security_scan(**kwargs)


def on_post_tool_call(tool_name: str, result: str, **kwargs: object) -> str:
    """Redact any secrets that may have leaked in tool output.

    Called after every tool invocation. Returns the redacted result.
    """
    return redact(result)
