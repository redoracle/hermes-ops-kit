"""
Hermes Ops Kit — Obsidian Documentation Sink

Writes sanitized documentation to Obsidian vault notes through mcp-vault.
This is the ONLY path through which Hermes writes to Obsidian.

Rules (spec section 25):
- Run secret scanner before every write.
- Reject if any raw secret pattern is detected.
- Reject if .env-like blocks are detected.
- Reject if Authorization headers are detected.
- Only write: provider name, rotation timestamp, old/new fingerprint,
  last4, status, smoke-test result, manual action required, risk note.
"""

from __future__ import annotations

import time

from security.fingerprints import secret_fingerprint  # pyright: ignore[reportMissingImports]
from security.secret_scanner import assert_clean  # pyright: ignore[reportMissingImports]


# Obsidian note paths (relative to vault root).
# Replace "<obsidian-vault>" with your vault path.
OBSIDIAN_NOTES = {
    "rotation_log": "<obsidian-vault>/HERMES_KEY_ROTATION.md",
    "status": "<obsidian-vault>/AI_PROVIDER_KEYS_STATUS.md",
    "runbook": "<obsidian-vault>/AI_PROVIDER_ROTATION_RUNBOOK.md",
    "risks": "<obsidian-vault>/AI_PROVIDER_RISKS.md",
}


def _format_markdown_entry(
    provider: str,
    status: str,
    mode: str = "",
    old_fingerprint: str | None = None,
    new_fingerprint: str | None = None,
    old_last4: str | None = None,
    new_last4: str | None = None,
    smoke_test: str = "",
    old_revoked: str = "",
    extra: str = "",
) -> str:
    """Build a sanitized markdown entry.  No raw secrets allowed."""
    ts = time.strftime("%Y-%m-%d — %H:%M UTC", time.gmtime())
    lines = [f"## {ts} — {provider.capitalize()} Key Rotation", ""]
    lines.append(f"**Status:** {status}")
    if mode:
        lines.append(f"**Mode:** {mode}")
    if old_fingerprint and old_last4:
        lines.append(f"**Old key:** {old_fingerprint} · last4={old_last4}")
    if new_fingerprint and new_last4:
        lines.append(f"**New key:** {new_fingerprint} · last4={new_last4}")
    if smoke_test:
        lines.append(f"**Smoke test:** {smoke_test}")
    if old_revoked:
        lines.append(f"**Old key revoked:** {old_revoked}")
    if extra:
        lines.append(extra)
    lines.append("")
    lines.append("**Raw secrets stored in note:** no")
    lines.append("")
    return "\n".join(lines)


def write_rotation_note(
    provider: str,
    status: str,
    *,
    mode: str = "",
    old_key: str | None = None,
    new_key: str | None = None,
    smoke_test: str = "",
    old_revoked: str = "",
    dry_run: bool = True,
) -> str:
    """Build a sanitized rotation note for Obsidian.

    Returns the markdown content.  If *dry_run* is False, also writes
    through mcp-vault (requires the MCP tool to be available).
    """
    old_fp, old_l4 = secret_fingerprint(old_key) if old_key else (None, None)
    new_fp, new_l4 = secret_fingerprint(new_key) if new_key else (None, None)

    content = _format_markdown_entry(
        provider=provider,
        status=status,
        mode=mode,
        old_fingerprint=old_fp,
        new_fingerprint=new_fp,
        old_last4=old_l4,
        new_last4=new_l4,
        smoke_test=smoke_test,
        old_revoked=old_revoked,
        extra="",
    )

    # Safety gate: reject if any raw secrets leaked into the note
    assert_clean(content, sink=f"obsidian:{provider}_rotation")

    if not dry_run:
        # mcp-vault write would go here — replaced with print for now
        print(f"[obsidian_sink] Would write to {OBSIDIAN_NOTES['rotation_log']}")
        # In production: mcp__obsidian-mcp-vault__append_note or write_note

    return content


def write_provider_status_note(
    provider: str,
    fingerprints: dict[str, tuple[str, str]],
    dry_run: bool = True,
) -> str:
    """Write provider key status to Obsidian."""
    ts = time.strftime("%Y-%m-%d", time.gmtime())
    lines = [f"# AI Provider Keys Status — {ts}", ""]
    lines.append(f"## {provider.capitalize()}")
    lines.append("")

    for name, (fp, l4) in sorted(fingerprints.items()):
        lines.append(f"- **{name}:** {fp} · last4={l4}")

    content = "\n".join(lines) + "\n"
    assert_clean(content, sink=f"obsidian:{provider}_status")

    if not dry_run:
        print(f"[obsidian_sink] Would write to {OBSIDIAN_NOTES['status']}")

    return content
