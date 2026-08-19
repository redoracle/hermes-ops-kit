#!/usr/bin/env python3
"""Hermes Ops Kit — Skill Factory.

Generates/updates SKILL.md files from existing commands and runbook notes.
Does NOT create an alternative skills system — only produces SKILL.md files
that Hermes loads natively via its built-in skill loader.

Usage:
    hermes-skill-factory from-command hermes-key-rotate
    hermes-skill-factory from-runbook AI_STUDIO/HERMES_KEY_ROTATION.md
    hermes-skill-factory list
    hermes-skill-factory validate hermes-key-rotate
"""

from __future__ import annotations


if __name__ == "__main__" and __spec__ is None:  # pragma: no cover
    raise SystemExit(
        "hermes-ops-kit modules must be run as package modules:\n"
        "  PYTHONPATH=<plugin-root> python -P -m hermes_ops_kit.<module>\n"
        "  (or use the hermes-ops-kit / hermes-usage / … console commands)"
    )

import argparse
import os
import sys
from datetime import datetime
from .ui.console import Console

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

# Shared console instance for consistent output formatting
console = Console()

# ─── Shared first-run bootstrap ──────────────────────────────────────
# Reusable "## First-run setup" body for any skill that depends on the
# Vaultwarden backend. Never hardcode a server URL or credentials here.

VAULTWARDEN_FIRST_RUN = """\
This skill resolves secrets through the Vaultwarden backend, bootstrapped from
`~/.hermes/.env`. **Before the first run, verify these keys exist. If any is missing, do NOT
hardcode a value — ask the user for it in conversation, write it to `~/.hermes/.env`, then
`chmod 600` the file.**

| Key | Description |
|---|---|
| `HERMES_SECRET_BACKEND` | Secret backend selector — set to `vaultwarden` |
| `VAULTWARDEN_SERVER_URL` | Vaultwarden/Bitwarden server URL (ask the user) |
| `VAULTWARDEN_USER` | Vault account email |
| `VAULTWARDEN_PASSWORD` | Master password |

Show which keys are already present (never prints secret values):

```bash
for k in HERMES_SECRET_BACKEND VAULTWARDEN_SERVER_URL VAULTWARDEN_USER VAULTWARDEN_PASSWORD; do
  grep -q "^${k}=" ~/.hermes/.env 2>/dev/null && echo "$k: set" || echo "$k: MISSING — ask the user"
done
```

> [!warning] Automated / non-interactive context: never run a blocking `read` prompt — it
> hangs or stores empty credentials. Ask the user in conversation, then persist.

For a human at an interactive terminal, this idempotent helper prompts only for the keys
that are still missing (the password is read silently):

```bash
# Run with bash, interactively. Adds only the keys not already present in ~/.hermes/.env.
env=~/.hermes/.env
mkdir -p ~/.hermes && touch "$env" && chmod 600 "$env"
grep -q '^HERMES_SECRET_BACKEND=' "$env" || printf 'HERMES_SECRET_BACKEND=%s\\n' vaultwarden >> "$env"
grep -q '^VAULTWARDEN_SERVER_URL=' "$env" || { read -rp  'Vaultwarden URL: '     v; printf 'VAULTWARDEN_SERVER_URL=%s\\n' "$v" >> "$env"; }
grep -q '^VAULTWARDEN_USER='       "$env" || { read -rp  'Vault account email: ' v; printf 'VAULTWARDEN_USER=%s\\n'       "$v" >> "$env"; }
grep -q '^VAULTWARDEN_PASSWORD='   "$env" || { read -rsp 'Vault master password: ' v; echo; printf 'VAULTWARDEN_PASSWORD=%s\\n' "$v" >> "$env"; }
chmod 600 "$env"
```"""

# ─── Command Metadata ────────────────────────────────────────────────

COMMAND_META: dict[str, dict] = {
    "hermes-key-rotate": {
        "title": "Key Rotation",
        "description": "Rotate, validate, store, and audit AI provider credentials backed by Vaultwarden.",
        "commands": [
            "hermes-key-rotate --doctor-secrets",
            "hermes-key-rotate --status",
            "hermes-key-rotate --render-env",
            "hermes-key-rotate --provider deepseek --dry-run",
            "hermes-key-rotate --provider openai --apply",
            "hermes-key-rotate --rollback",
        ],
        "related": ["hermes-usage", "hermes-assistant-manager"],
        "first_run": VAULTWARDEN_FIRST_RUN,
        "danger_notes": [
            "Never revoke old key before smoke test passes.",
            "Never pass raw API keys as CLI arguments.",
        ],
    },
    "hermes-usage": {
        "title": "Usage Metrics",
        "description": "Provider health, rate limits, usage costs, and routing recommendations.",
        "commands": [
            "hermes-usage",
            "hermes-usage --compact",
            "hermes-usage --json",
            "hermes-usage --models",
            "hermes-usage --costs",
            "hermes-usage -p github",
        ],
        "related": ["hermes-key-rotate", "hermes-route-manager"],
        "danger_notes": [],
    },
    "hermes-assistant-manager": {
        "title": "Assistant Management",
        "description": "Manage remote Hermes assistants: add, edit, remove, ping, discover.",
        "commands": [
            "hermes-assistant-manager list",
            "hermes-assistant-manager get assistant-id",
            "hermes-assistant-manager ping assistant-id",
            "hermes-assistant-manager discover <assistant-url>",
            "hermes-assistant-manager template assistant-id --write assistant-id",
            "hermes-assistant-manager validate",
            "hermes-assistant-manager doctor",
        ],
        "related": ["hermes-route-manager", "hermes-export"],
        "danger_notes": [
            "Assistant configs reference env var names only — never store raw secrets.",
            "Secret scanner runs before every write.",
        ],
    },
    "hermes-route-manager": {
        "title": "Route Management",
        "description": "Configure Hermes routing: primary model, auxiliary routes, fallback chain, profiles.",
        "commands": [
            "hermes-route-manager show",
            "hermes-route-manager doctor",
            "hermes-route-manager apply-profile cheap",
            "hermes-route-manager set-primary copilot gpt-5.4-mini",
            "hermes-route-manager fallback list",
        ],
        "related": ["hermes-usage", "hermes-assistant-manager"],
        "danger_notes": [],
    },
    "hermes-export": {
        "title": "Export Center",
        "description": "Structured exports: usage reports, security briefings, audit logs, task reports.",
        "commands": [
            "hermes-export report usage --format md",
            "hermes-export report security --assistant assistant-id",
            'hermes-export contact-briefing person "John Doe"',
            "hermes-export audit --since 7d",
            "hermes-export list",
        ],
        "related": ["hermes-usage", "hermes-assistant-manager"],
        "danger_notes": [],
    },
    "ops-kit-doctor": {
        "title": "Ops Kit Doctor",
        "description": "Unified end-to-end diagnostic: CORE, ROUTES, ASSISTANTS, SECRETS, WARNINGS.",
        "commands": [
            "hermes-ops-kit doctor",
            "hermes-ops-kit status",
            "hermes-ops-kit audit tail",
        ],
        "related": ["hermes-usage", "hermes-key-rotate", "hermes-assistant-manager"],
        "danger_notes": [],
    },
}


# ─── Runbook extraction ───────────────────────────────────────────────


def _extract_from_runbook(runbook_path: str) -> dict:
    """Extract title, description, and commands from an Obsidian runbook note."""
    from pathlib import Path

    paths = [
        runbook_path,
        os.path.join(os.path.expanduser("~/GIT/INFRA"), runbook_path),
    ]
    content = ""
    for p in paths:
        if os.path.exists(p):
            content = Path(p).read_text()
            break

    if not content:
        return {}

    # Extract title from first H1
    title = ""
    description = ""
    commands: list[str] = []
    in_code_block = False
    code_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("# ") and not title:
            title = line[2:].strip()
        elif line.startswith("```bash"):
            in_code_block = True
            code_lines = []
        elif line.startswith("```") and in_code_block:
            in_code_block = False
            for cl in code_lines:
                cl = cl.strip()
                if cl and not cl.startswith("#"):
                    commands.append(cl)
        elif in_code_block:
            code_lines.append(line)
        elif line.startswith(">") and not description:
            description = line.lstrip("> ").strip()

    return {
        "title": title or os.path.basename(runbook_path).replace(".md", ""),
        "description": description or "Operational runbook.",
        "commands": commands,
        "related": [],
        "danger_notes": [],
    }


# ─── SKILL.md Generation ──────────────────────────────────────────────


def _generate_skill(meta: dict, skill_name: str) -> str:
    """Generate SKILL.md content from metadata."""
    title = meta.get("title", skill_name)
    desc = meta.get("description", "")
    commands = meta.get("commands", [])
    related = meta.get("related", [])
    dangers = meta.get("danger_notes", [])
    first_run = meta.get("first_run")

    lines = [
        f"# {title}",
        "",
        desc,
        "",
    ]
    if first_run:
        lines += ["## First-run setup", "", first_run.strip(), ""]
    lines += [
        "## Commands",
        "",
    ]
    for c in commands:
        lines.append(f"```bash\n{c}\n```")
        lines.append("")
    if not commands:
        lines.append("_No commands listed_")
        lines.append("")

    lines.append("## Related")
    lines.append("")
    if related:
        for r in related:
            lines.append(f"- [[{r}]]")
    else:
        lines.append("_None_")
    lines.append("")

    if dangers:
        for d in dangers:
            lines.append(f"> [!warning] {d}")
        lines.append("")

    lines.append(
        f"> Generated by hermes-skill-factory on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    return "\n".join(lines)


def _write_skill(skill_name: str, content: str) -> str:
    """Write SKILL.md to skills/<name>/SKILL.md."""
    skill_dir = os.path.join(SKILLS_DIR, skill_name)
    os.makedirs(skill_dir, exist_ok=True)
    path = os.path.join(skill_dir, "SKILL.md")
    with open(path, "w") as f:
        f.write(content + "\n")
    return path


# ─── Commands ──────────────────────────────────────────────────────────


def cmd_from_command(args: argparse.Namespace) -> None:
    """Generate SKILL.md from built-in command metadata."""
    cmd_name = args.command_name
    meta = COMMAND_META.get(cmd_name)
    if not meta:
        console.print_error(f"Unknown command: {cmd_name}")
        console.print(f"Known: {list(COMMAND_META.keys())}")
        sys.exit(1)

    skill_name = args.name or cmd_name
    content = _generate_skill(meta, skill_name)
    if args.dry_run:
        console.print(content)
        console.print(f"\nWould write to: skills/{skill_name}/SKILL.md")
    else:
        path = _write_skill(skill_name, content)
        console.print(f"Created: {path}")


def cmd_from_runbook(args: argparse.Namespace) -> None:
    """Generate SKILL.md from an Obsidian runbook note."""
    meta = _extract_from_runbook(args.runbook_path)
    if not meta:
        console.print_error(f"Could not read runbook: {args.runbook_path}")
        sys.exit(1)

    skill_name = args.name or meta["title"].lower().replace(" ", "-")
    content = _generate_skill(meta, skill_name)
    if args.dry_run:
        console.print(content)
        console.print(f"\nWould write to: skills/{skill_name}/SKILL.md")
    else:
        path = _write_skill(skill_name, content)
        console.print(f"Created: {path}")


def cmd_list(_args: argparse.Namespace) -> None:
    """List all generated skills."""
    if not os.path.exists(SKILLS_DIR):
        console.print("No skills directory yet")
        return
    skills = [
        d
        for d in os.listdir(SKILLS_DIR)
        if os.path.isdir(os.path.join(SKILLS_DIR, d))
        and os.path.exists(os.path.join(SKILLS_DIR, d, "SKILL.md"))
    ]
    if not skills:
        console.print("No skills generated yet")
        return
    console.print(f"SKILLS ({len(skills)})\n")
    for s in sorted(skills):
        path = os.path.join(SKILLS_DIR, s, "SKILL.md")
        size = os.path.getsize(path)
        console.print(f"  ● {s:<30s} {size:>5d} bytes")


def cmd_validate(args: argparse.Namespace) -> None:
    """Validate an existing SKILL.md."""
    skill_name = args.skill_name
    path = os.path.join(SKILLS_DIR, skill_name, "SKILL.md")
    if not os.path.exists(path):
        console.print_error(f"Skill not found: {skill_name}")
        console.print_error(f"Path: {path}")
        sys.exit(1)

    content = open(path).read()
    checks = [
        ("Has title (H1)", content.startswith("# ")),
        ("Has description", len(content.split("\n")) > 2),
        ("Under 10KB", len(content) < 10240),
        ("No raw 'sk-' secrets", "sk-" not in content or "<REDACTED>" in content),
    ]
    for label, ok in checks:
        console.print(f"  {'✅' if ok else '❌'} {label}")
    if all(ok for _, ok in checks):
        console.print(f"\n  Skill '{skill_name}' is valid ✅")


def cmd_audit(_args: argparse.Namespace) -> None:
    """Audit: show which commands have skills and which don't."""
    existing = set()
    if os.path.exists(SKILLS_DIR):
        existing = {
            d
            for d in os.listdir(SKILLS_DIR)
            if os.path.isdir(os.path.join(SKILLS_DIR, d))
            and os.path.exists(os.path.join(SKILLS_DIR, d, "SKILL.md"))
        }
    console.print(f"Commands: {len(COMMAND_META)} | Skills: {len(existing)}\n")
    for cmd in sorted(COMMAND_META):
        has_skill = cmd in existing
        console.print(f"  {'✅' if has_skill else '❌'} {cmd}")


# ─── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Hermes Ops Kit — Skill Factory")
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview without writing"
    )
    parser.add_argument(
        "--name", help="Custom skill name (default: derived from command)"
    )
    sub = parser.add_subparsers(dest="action")
    sub.required = True

    fc = sub.add_parser("from-command", help="Generate from built-in command")
    fc.add_argument("command_name", choices=list(COMMAND_META.keys()))

    fr = sub.add_parser("from-runbook", help="Generate from Obsidian runbook")
    fr.add_argument("runbook_path", help="Path to Obsidian runbook note")

    sub.add_parser("list", help="List generated skills")
    sub.add_parser("audit", help="Audit: which commands have skills")

    val = sub.add_parser("validate", help="Validate a generated skill")
    val.add_argument("skill_name")

    args = parser.parse_args()

    if args.action == "from-command":
        cmd_from_command(args)
    elif args.action == "from-runbook":
        cmd_from_runbook(args)
    elif args.action == "list":
        cmd_list(args)
    elif args.action == "audit":
        cmd_audit(args)
    elif args.action == "validate":
        cmd_validate(args)


if __name__ == "__main__":
    main()
