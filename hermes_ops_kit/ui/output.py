"""Hermes Ops Kit — Unified output helpers.

print_result(), print_error(), print_warning(), print_table() —
all aware of JSON vs human mode via the Console abstraction.
"""

from __future__ import annotations

from typing import Any

from ..ui.console import Console  # pyright: ignore[reportMissingImports]
from ..ui.json_output import ok_envelope, error_envelope  # pyright: ignore[reportMissingImports]


def print_result(
    console: Console,
    command: str,
    data: Any,
    warnings: list[str] | None = None,
) -> None:
    """Print a command result in the current output mode."""
    if console.json_mode:
        console.print_json(ok_envelope(command, data, warnings))
    elif isinstance(data, dict):
        # Human mode: pretty-print dict
        console.print("{")
        for k, v in data.items():
            if isinstance(v, list):
                console.print(f'  "{k}": [{len(v)} items]')
            elif isinstance(v, dict):
                console.print(f'  "{k}": {{...}}')
            else:
                console.print(f'  "{k}": {v!r}')
        console.print("}")
    elif isinstance(data, list):
        for item in data:
            console.print(f"  {item}")
    else:
        console.print(str(data))


def print_error_result(
    console: Console,
    command: str,
    errors: list[dict[str, str]],
    warnings: list[str] | None = None,
) -> None:
    """Print a command error in the current output mode."""
    if console.json_mode:
        console.print_json(error_envelope(command, errors, warnings))
    else:
        for err in errors:
            msg = f"{err.get('code', 'error')}: {err.get('message', '')}"
            hint = err.get("hint", "")
            console.print_error(msg)
            if hint:
                console.print(
                    f"  fix: {hint}",
                    file=__import__("sys").stderr
                    if hasattr(__import__("sys"), "stderr")
                    else None,
                )


def print_table(
    console: Console,
    headers: list[str],
    rows: list[list[str]],
    *,
    title: str = "",
) -> None:
    """Print a simple aligned table."""
    if console.json_mode:
        # JSON mode: convert to list of dicts
        result = [dict(zip(headers, row)) for row in rows]
        console.print_json({"table": result})
        return

    if title:
        console.print(console.header(title))

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))

    # Header
    header_line = "  " + "  ".join(
        h.ljust(col_widths[i]) for i, h in enumerate(headers)
    )
    console.print(console.bold(header_line))

    # Separator
    sep = "  " + "  ".join("─" * col_widths[i] for i in range(len(headers)))
    console.print(console.dim(sep))

    # Rows
    for row in rows:
        line = "  " + "  ".join(
            str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)
        )
        console.print(line)
