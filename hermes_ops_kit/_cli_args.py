"""Strict flag checking for the hand-dispatched CLI commands.

A command that silently ignores a flag it does not understand is dangerous when the flag
looked protective (``--dry-run``, ``-h``): the action runs anyway. Every ``-`` token must
therefore be a flag the command declares, and a flag that takes a value must be followed by
a value (never by another flag), so a value can neither hide nor smuggle an option.
"""

from __future__ import annotations

VALUE = True  # the flag takes a value
SWITCH = False

# Flags the chat command (``slash.py``) lets through on every allowlisted command. Several
# commands ignore them, so the guarded ones accept them as no-ops rather than break chat.
PRESENTATION_FLAGS = frozenset(
    {"--json", "--compact", "--verbose", "-v", "--plain", "--no-color"}
)


def check_flags(
    label: str,
    accepted: dict[str, bool],
    args: list[str],
    usage: str | None = None,
    ignored: frozenset[str] = frozenset(),
) -> int | None:
    """None when every ``-`` token is acceptable; else print why and return an exit code.

    ``-h``/``--help`` prints ``usage`` and returns 0 when a usage string is given.
    ``ignored`` flags are accepted and have no effect.
    """
    i = 0
    while i < len(args):
        token = args[i]
        if token in accepted:
            if accepted[token]:
                if i + 1 >= len(args) or args[i + 1].startswith("-"):
                    print(f"ERROR: option {token} for '{label}' needs a value")
                    return 2
                i += 2
            else:
                i += 1
            continue
        if token in ignored:
            i += 1
            continue
        if usage and token in ("-h", "--help"):
            print(f"Usage: {usage}")
            return 0
        if token.startswith("-"):
            print(f"ERROR: unknown option for '{label}': {token}")
            known = sorted({*accepted, *ignored})
            print(f"Accepted: {', '.join(known) or '(none)'}")
            if usage:
                print(f"Usage: {usage}")
            return 2
        i += 1
    return None
