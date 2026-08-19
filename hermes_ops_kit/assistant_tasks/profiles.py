"""Hermes Ops Kit — Vault Scheduler Profiles.

Profile loading, validation, assistant capability checking.
"""

from __future__ import annotations

import os
from typing import Any

PROFILE_PATHS = [
    os.path.expanduser("~/.hermes/ops-kit/obsidian_maintenance.yaml"),
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "obsidian_maintenance.yaml",
    ),
]

DEFAULT_PROFILES: dict[str, dict[str, Any]] = {
    "generic-obsidian-daily": {
        "enabled": True,
        "assistant_id": "assistant-id",
        "schedule": "daily at 06:30",
        "capability": "vault_maintenance",
        "operations": [
            "update_indexes",
            "find_stale_notes",
            "find_broken_links",
            "find_missing_metadata",
            "find_low_confidence_notes",
        ],
        "constraints": {
            "no_secret_storage": True,
            "preserve_existing_notes": True,
            "patch_only": True,
            "require_source_metadata": True,
        },
        "output": {"mode": "summary", "write_report": True},
    },
    "weekly-index-audit": {
        "enabled": True,
        "assistant_id": "assistant-id",
        "schedule": "weekly Monday at 07:00",
        "capability": "vault_maintenance",
        "operations": [
            "update_indexes",
            "find_duplicate_notes",
            "find_missing_backlinks",
            "find_conflicting_claims",
            "validate_frontmatter",
            "generate_followups",
        ],
        "constraints": {"no_secret_storage": True, "patch_only": True},
        "output": {"mode": "summary", "write_report": True},
    },
}


def load_profiles() -> dict[str, dict[str, Any]]:
    """Load assistant task profiles from config."""
    for p in PROFILE_PATHS:
        if os.path.exists(p):
            try:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                with open(p) as f:
                    cfg = _yaml.safe_load(f) or {}
                return cfg.get("profiles", DEFAULT_PROFILES)
            except Exception:
                pass
    return dict(DEFAULT_PROFILES)


def validate_profile(
    profile_name: str, profile: dict[str, Any]
) -> tuple[bool, list[str]]:
    """Validate a maintenance profile against assistant registry."""
    issues = []

    assistant_id = profile.get("assistant_id", "")
    if not assistant_id:
        issues.append("missing assistant_id")

    capability = profile.get("capability", "")
    if not capability:
        issues.append("missing capability")

    # Validate assistant exists
    if assistant_id:
        try:
            from ..assistants.registry import (
                get_assistant,
            )  # pyright: ignore[reportMissingImports]

            cfg = get_assistant(assistant_id)
            if not cfg:
                issues.append(f"assistant '{assistant_id}' not found in registry")
            elif not cfg.enabled:
                issues.append(f"assistant '{assistant_id}' is disabled")
            elif capability:
                cap_ids = {
                    c["id"] if isinstance(c, dict) else c for c in cfg.capabilities
                }
                if capability not in cap_ids:
                    issues.append(
                        f"assistant '{assistant_id}' missing capability '{capability}'"
                    )
        except Exception as e:
            issues.append(f"registry error: {e}")

    # Validate operations
    valid_ops = {
        "update_indexes",
        "find_stale_notes",
        "find_duplicate_notes",
        "find_broken_links",
        "find_missing_backlinks",
        "find_missing_metadata",
        "find_low_confidence_notes",
        "find_conflicting_claims",
        "find_notes_needing_review",
        "validate_frontmatter",
        "generate_followups",
        "generate_maintenance_report",
    }
    for op in profile.get("operations", []):
        if op not in valid_ops:
            issues.append(f"unknown operation: {op}")

    return len(issues) == 0, issues
