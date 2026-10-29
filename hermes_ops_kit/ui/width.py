"""Terminal display width, for output whose columns must line up.

``len()`` counts code points, not terminal cells: an emoji such as ``✅`` takes two cells,
a combining mark none. Pad and truncate by cells instead.

Stick to characters whose width every terminal agrees on. A glyph that only becomes an
emoji through a variation selector (``⚠️`` is U+26A0 + U+FE0F, ``ℹ️`` is U+2139 + U+FE0F)
is drawn 2 cells wide by some terminals and 1 by others, so no padding can align it.
Use a wide-by-definition emoji (``🟡``, ``🔵``, ``✅``, ``🔴``, ``⛔``) in tables.
"""

from __future__ import annotations

import re
import unicodedata

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def display_width(text: str) -> int:
    """Terminal cells ``text`` occupies: wide/fullwidth characters count 2, marks 0."""
    cells = 0
    for ch in _ANSI.sub("", text):  # colour codes take no cells
        if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue  # combining marks, variation selectors, zero-width joiners
        cells += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return cells


def is_unambiguous_icon(icon: str) -> bool:
    """True for one wide character that needs no variation selector (safe in a table)."""
    return len(icon) == 1 and unicodedata.east_asian_width(icon) == "W"


def pad(text: str, width: int, align: str = "<") -> str:
    """``text`` padded with spaces to ``width`` cells (``<`` left, ``>`` right-aligned)."""
    gap = max(0, width - display_width(text))
    return " " * gap + text if align == ">" else text + " " * gap


def truncate(text: str, width: int) -> str:
    """``text`` cut to at most ``width`` cells, ending in ``…`` when something was cut."""
    if display_width(text) <= width or "\x1b" in text:
        return text  # coloured cells are short status words; never cut an escape sequence
    out, cells = "", 0
    for ch in text:
        step = display_width(ch)
        if cells + step > width - 1:
            break
        out, cells = out + ch, cells + step
    return out + "…"
