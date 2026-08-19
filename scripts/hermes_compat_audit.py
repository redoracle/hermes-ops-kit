#!/usr/bin/env python3
"""Hermes Ops Kit — Hermes Agent compatibility audit (grounded release fetcher).

Fetches the latest hermes-agent releases from the GitHub API and compares them
against the ops-kit compatibility manifest (hermes_ops_kit/config/compat.yaml). Produces a
structured summary the hermes-compat-audit skill reasons over — so audit
findings are grounded in real release data, not the model's memory.

Usage:
    python3 scripts/hermes_compat_audit.py                 # readable summary
    python3 scripts/hermes_compat_audit.py --json          # machine-readable
    python3 scripts/hermes_compat_audit.py --releases 3    # last N releases

Never raises on network/rate-limit errors — prints a clear error envelope and
exits 0 so the skill can degrade gracefully (e.g. offline → audit against the
last known manifest only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
COMPAT_YAML = os.path.join(ROOT_DIR, "hermes_ops_kit", "config", "compat.yaml")
RELEASES_API = "https://api.github.com/repos/NousResearch/hermes-agent/releases"


def _fetch_json(url: str, timeout: int = 20) -> tuple[dict | list | None, str | None]:
    """Return (data, error). Never raises."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "hermes-ops-kit-compat-audit",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:300]
        return None, f"HTTP {e.code}: {body}"
    except Exception as e:
        return None, f"{e.__class__.__name__}: {e}"


def _load_compat() -> dict:
    try:
        import yaml  # type: ignore

        with open(COMPAT_YAML) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _summarize_release(rel: dict) -> dict:
    body = (rel.get("body") or "").strip()
    return {
        "tag": rel.get("tag_name"),
        "name": rel.get("name"),
        "published_at": rel.get("published_at"),
        "prerelease": rel.get("prerelease", False),
        "html_url": rel.get("html_url"),
        "body_chars": len(body),
        "body_preview": body[:1200],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Agent compatibility audit")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON")
    parser.add_argument(
        "--releases", type=int, default=3, help="Number of releases to summarise"
    )
    args = parser.parse_args()

    compat = _load_compat()
    api_url = compat.get("releases_api") or RELEASES_API
    data, err = _fetch_json(f"{api_url}?per_page={max(1, args.releases)}")
    releases = []
    fetch_error = err
    if isinstance(data, list):
        releases = [_summarize_release(r) for r in data[: args.releases]]

    latest = releases[0] if releases else None
    target = (compat.get("target_hermes_version") or "").strip() if compat else ""

    # Coverage tally from the manifest
    features = compat.get("features", []) if compat else []
    tally = {"covered": 0, "partial": 0, "missing": 0, "not-ops-kit-lane": 0}
    for f in features:
        s = f.get("status", "missing")
        tally[s] = tally.get(s, 0) + 1

    result = {
        "ops_kit_target_hermes_version": target,
        "ops_kit_codename": compat.get("target_hermes_codename"),
        "latest_release": latest,
        "target_matches_latest": bool(
            latest
            and target
            and (
                target in (latest.get("tag") or "")
                or target in (latest.get("name") or "")
            )
        ),
        "recent_releases": releases,
        "fetch_error": fetch_error,
        "coverage_tally": tally,
        "features": features,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    # Readable
    print("=== Hermes Ops Kit — Compatibility Audit ===")
    print(
        f"Target Hermes version: {target} ({compat.get('target_hermes_codename', '?')})"
    )
    if fetch_error:
        print(f"GitHub fetch failed (offline?): {fetch_error}")
        print("Auditing against the local manifest only.")
    elif latest:
        match = "MATCH" if result["target_matches_latest"] else "DRIFT"
        print(
            f"Latest release: {latest.get('tag')} ({latest.get('published_at')}) [{match}]"
        )
        print(f"  url: {latest.get('html_url')}")
    else:
        print("No releases found (GitHub returned an empty list or non-list response).")
        print("Auditing against the local manifest only.")
    print(f"\nCoverage tally: {tally}")
    print("\nRecent releases:")
    for r in releases:
        print(
            f"  - {r.get('tag')} ({r.get('published_at')}) prerelease={r.get('prerelease')}"
        )
    print("\nFeature coverage:")
    for f in features:
        print(f"  [{f.get('status', '?'):>16}] {f.get('area', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
