"""Hermes Ops Kit — Unified output helpers.

print_result(), print_error(), print_warning(), print_table() —
all aware of JSON vs human mode via the Console abstraction.
"""

from __future__ import annotations

from typing import Any

from ..ui.console import Console  # pyright: ignore[reportMissingImports]
from ..ui.json_output import ok_envelope, error_envelope  # pyright: ignore[reportMissingImports]
from ..ui.width import display_width, pad, truncate


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


class Table:
    """Column layout that pads by terminal cells, so wide characters stay aligned.

    Build it from every row first (widths come from the data), then format rows one at a
    time: callers that print detail lines between rows cannot use ``print_table``.
    """

    def __init__(
        self,
        headers: list[str],
        rows: list[list[str]],
        *,
        aligns: str = "",
        max_width: int = 48,
    ) -> None:
        count = len(headers)
        self.aligns = (aligns + "<" * count)[:count]
        self.headers = headers
        self.widths = [
            min(max_width, max([display_width(h)] + [display_width(str(r[i])) for r in rows if i < len(r)]))
            for i, h in enumerate(headers)
        ]

    def _line(self, cells: list[str]) -> str:
        parts = [
            pad(truncate(str(cells[i]), self.widths[i]) if i < len(cells) else "", self.widths[i], self.aligns[i])
            for i in range(len(self.widths))
        ]
        return ("  " + "  ".join(parts)).rstrip()

    def header(self) -> str:
        return self._line(self.headers)

    def separator(self) -> str:
        return "  " + "  ".join("─" * w if w else "" for w in self.widths).rstrip()

    def row(self, cells: list[str]) -> str:
        return self._line(cells)


def print_table(
    console: Console,
    headers: list[str],
    rows: list[list[str]],
    *,
    title: str = "",
    aligns: str = "",
) -> None:
    """Print a simple aligned table."""
    if console.json_mode:
        # JSON mode: convert to list of dicts
        result = [dict(zip(headers, row)) for row in rows]
        console.print_json({"table": result})
        return

    if title:
        console.print(console.header(title))

    table = Table(headers, rows, aligns=aligns)
    console.print(console.bold(table.header()))
    console.print(console.dim(table.separator()))
    for row in rows:
        console.print(table.row(row))
