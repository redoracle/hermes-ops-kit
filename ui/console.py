"""Hermes Ops Kit — Console abstraction.

Single Console class that handles: TTY detection, NO_COLOR, output mode
dispatch (human/JSON/plain/compact), and terminal width.

Usage:
    from ui.console import Console
    console = Console(json_mode=False, plain=False)
    console.print("Hello")
    console.print_json({"ok": True})
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any


class Console:
    """Unified console output for all Hermes Ops Kit commands."""

    def __init__(
        self,
        *,
        json_mode: bool = False,
        plain: bool = False,
        compact: bool = False,
        quiet: bool = False,
        no_color: bool = False,
    ) -> None:
        self.json_mode = json_mode
        self.plain = plain
        self.compact = compact
        self.quiet = quiet

        # NO_COLOR support (spec: https://no-color.org)
        self.no_color = no_color or os.environ.get("NO_COLOR", "") != ""

        # TTY detection — disable rich output for pipes/CI
        self.is_tty = sys.stdout.isatty()

        # Use plain mode if not a TTY (piped, CI, etc.)
        if not self.is_tty and not json_mode:
            self.plain = True

    # ── Output modes ──────────────────────────────────────────────

    def print(self, *args, **kwargs) -> None:
        """Print to stdout. Suppressed in quiet mode."""
        if self.quiet:
            return
        print(*args, **kwargs)

    def print_json(self, data: dict[str, Any]) -> None:
        """Print JSON to stdout. No ANSI, no prose. Always goes to stdout."""
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))

    def print_error(self, message: str) -> None:
        """Print error to stderr."""
        prefix = "ERROR: " if self.plain else "✗ ERROR: "
        if self.no_color:
            prefix = "ERROR: "
        print(prefix + message, file=sys.stderr)

    def print_warning(self, message: str) -> None:
        """Print warning to stderr."""
        if self.quiet:
            return
        prefix = "WARNING: " if self.plain else "⚠ WARNING: "
        if self.no_color:
            prefix = "WARNING: "
        print(prefix + message, file=sys.stderr)

    # ── Helpers ───────────────────────────────────────────────────

    def status_icon(self, status: str) -> str:
        """Return a status icon for the given state.

        Uses Unicode only when not in no_color/plain mode.
        """
        if self.no_color or self.plain:
            return {
                "online": "[OK]",
                "ready": "[OK]",
                "offline": "[--]",
                "error": "[!!]",
                "warning": "[??]",
                "blocked": "[XX]",
                "disabled": "[  ]",
            }.get(status, "[?]")
        return {
            "online": "●",
            "ready": "●",
            "offline": "○",
            "error": "✗",
            "warning": "⚠",
            "blocked": "◉",
            "disabled": "○",
        }.get(status, "●")

    def header(self, text: str) -> str:
        """Format a section header."""
        if self.no_color or self.plain:
            return text.upper()
        return f"\033[1m{text.upper()}\033[0m"

    def dim(self, text: str) -> str:
        """Format dim/secondary text."""
        if self.no_color or self.plain:
            return text
        return f"\033[2m{text}\033[0m"

    def green(self, text: str) -> str:
        if self.no_color or self.plain:
            return text
        return f"\033[32m{text}\033[0m"

    def yellow(self, text: str) -> str:
        if self.no_color or self.plain:
            return text
        return f"\033[33m{text}\033[0m"

    def red(self, text: str) -> str:
        if self.no_color or self.plain:
            return text
        return f"\033[31m{text}\033[0m"

    def blue(self, text: str) -> str:
        if self.no_color or self.plain:
            return text
        return f"\033[34m{text}\033[0m"

    def bold(self, text: str) -> str:
        if self.no_color or self.plain:
            return text
        return f"\033[1m{text}\033[0m"


# ─── Module-level convenience ─────────────────────────────────────


def detect_console(args: Any) -> Console:
    """Build a Console from argparse namespace.

    Checks for common flags: args.json, args.plain, args.compact,
    args.quiet, args.no_color.
    """
    return Console(
        json_mode=getattr(args, "json", False),
        plain=getattr(args, "plain", False) or getattr(args, "no_color", False),
        compact=getattr(args, "compact", False),
        quiet=getattr(args, "quiet", False),
        no_color=getattr(args, "no_color", False),
    )
