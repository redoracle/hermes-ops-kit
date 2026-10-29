"""Supply-chain invariants for the CI and release configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))


def _uses():
    for path in WORKFLOWS:
        for number, line in enumerate(path.read_text().splitlines(), 1):
            match = re.match(r"\s*-?\s*uses:\s*(\S+)", line)
            if match:
                yield path.name, number, match.group(1)


def test_every_action_is_pinned_to_a_full_commit_sha():
    unpinned = [
        f"{name}:{number}: {ref}"
        for name, number, ref in _uses()
        if not re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", ref)
    ]
    assert unpinned == []


def test_the_release_job_waits_for_the_release_environment():
    release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    assert release["jobs"]["release"]["environment"] == "release"


def test_the_release_job_installs_node_dependencies_from_the_lockfile():
    text = (ROOT / ".github/workflows/release.yml").read_text()
    assert "npm ci --ignore-scripts" in text
    assert "npm install" not in text


def test_the_lockfile_matches_package_json():
    package = json.loads((ROOT / "package.json").read_text())
    lock = json.loads((ROOT / "package-lock.json").read_text())
    root = lock["packages"][""]
    assert root["devDependencies"] == package["devDependencies"]
    resolved = [
        entry["resolved"] for entry in lock["packages"].values() if "resolved" in entry
    ]
    assert resolved and all(
        url.startswith("https://registry.npmjs.org/") for url in resolved
    )


def test_dependabot_covers_every_ecosystem_with_a_cooldown():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    updates = {u["package-ecosystem"]: u for u in config["updates"]}
    assert set(updates) == {"pip", "npm", "github-actions"}
    assert all(u["cooldown"]["default-days"] == 7 for u in updates.values())
