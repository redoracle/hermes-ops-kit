"""Hermes Ops Kit — Plugin Scanner: Orchestrator.

Coordinates scan categories by profile, aggregates findings,
computes risk scores, and produces ScanResult objects.
"""

from __future__ import annotations

import os
import hashlib
import json
import logging
import sys
import time
from typing import Any

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ),
)

from security.plugin_scanner.findings import (  # pyright: ignore[reportMissingImports]
    Finding,
    RiskLevel,
    ScanCategory,
    ScanProfile,
    ScanResult,
    Severity,
)
from security.plugin_scanner.cache import (  # pyright: ignore[reportMissingImports]
    cache_lookup,
    cache_store,
    compute_cache_key,
)
from security.plugin_scanner.categories.secrets import run as secrets_run  # pyright: ignore[reportMissingImports]
from security.plugin_scanner.categories.secrets import detect_plugin_type  # pyright: ignore[reportMissingImports]
from security.plugin_scanner.categories.policy import run as policy_run  # pyright: ignore[reportMissingImports]
from security.plugin_scanner.policy import apply_rule_overrides, policy_fingerprint  # pyright: ignore[reportMissingImports]


# Import single source of truth for scanner version
from security.plugin_scanner.cache import SCANNER_VERSION as _SCANNER_VERSION  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)

# ── Category Registry ────────────────────────────────────────────────

CATEGORY_RUNNERS: dict[str, Any] = {
    ScanCategory.SECRETS.value: secrets_run,
    ScanCategory.POLICY.value: policy_run,
    # Future categories:
    # ScanCategory.CODE.value: code,
    # ScanCategory.DEPENDENCIES.value: dependencies,
    # ScanCategory.BEHAVIOR.value: behavior,
    # ScanCategory.REPUTATION.value: reputation,
}

CATEGORY_WEIGHTS: dict[str, float] = {
    ScanCategory.SECRETS.value: 2.0,
    ScanCategory.POLICY.value: 1.5,
    ScanCategory.CODE.value: 1.0,
    ScanCategory.DEPENDENCIES.value: 1.5,
    ScanCategory.BEHAVIOR.value: 2.0,
    ScanCategory.REPUTATION.value: 0.8,
}

_CATEGORY_KWARGS: dict[str, set[str]] = {
    ScanCategory.SECRETS.value: {"plugin_type", "skip_patterns", "use_gitleaks"},
    ScanCategory.POLICY.value: {
        "plugin_type",
        "skip_patterns",
        "use_semgrep",
        "use_bandit",
        "semgrep_rules",
    },
}


# ── Scoring ──────────────────────────────────────────────────────────


def _compute_score(findings: list[Finding]) -> float:
    """Compute aggregated risk score from findings.

    score = Σ (finding.severity.multiplier × category_weight × 10)
    """
    total = 0.0
    for f in findings:
        cat_weight = CATEGORY_WEIGHTS.get(f.category, 1.0)
        total += f.severity.multiplier * cat_weight * 10
    return round(total, 1)


def _compute_risk_level(findings: list[Finding], score: float) -> RiskLevel:
    """Determine overall risk level from findings and score.

    Priority:
      1. Any single CRITICAL finding → CRITICAL (unconditional)
      2. Any single HIGH finding → at least HIGH
      3. Score-based fallback, capped to prevent low-severity
         aggregation from inflating the overall risk.
    """
    has_critical = any(f.risk_level == RiskLevel.CRITICAL for f in findings)
    has_high = any(f.risk_level == RiskLevel.HIGH for f in findings)

    # Unconditional: any CRITICAL finding → CRITICAL
    if has_critical:
        return RiskLevel.CRITICAL

    # Score-based fallback
    score_level = RiskLevel.from_score(score)

    # Cap: if no individual finding is CRITICAL, can't be CRITICAL.
    # Downgrade to HIGH and continue checking for further caps.
    if score_level == RiskLevel.CRITICAL:
        score_level = RiskLevel.HIGH

    # Cap: if no individual finding is HIGH or above, can't be HIGH.
    if score_level == RiskLevel.HIGH and not has_high:
        score_level = RiskLevel.MEDIUM

    return score_level


def _scan_result_to_cache_dict(result: ScanResult) -> str:
    """Map result to scan_result string for cache."""
    if result.risk_level == RiskLevel.CRITICAL:
        return "blocked"
    if result.risk_level in (RiskLevel.HIGH, RiskLevel.MEDIUM):
        return "warning"
    return "clean"


def _scan_context(
    plugin_name: str, categories: list[str], profile: str, kwargs: dict[str, Any]
) -> str:
    """Hash all inputs that can change scanner output without changing files."""
    relevant_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key
        in {
            "use_semgrep",
            "use_bandit",
            "use_gitleaks",
            "skip_patterns",
            "semgrep_rules",
        }
    }
    payload = {
        "plugin_name": plugin_name,
        "categories": sorted(categories),
        "profile": profile,
        "options": relevant_kwargs,
        "policy": policy_fingerprint(plugin_name),
    }
    encoded = json.dumps(
        payload, sort_keys=True, default=str, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


# ── Scanning ─────────────────────────────────────────────────────────


def scan_plugin(
    plugin_name: str,
    plugin_path: str,
    *,
    categories: list[str] | None = None,
    profile: str = "startup",
    force: bool = False,
    use_cache: bool = True,
    **kwargs: Any,
) -> ScanResult:
    """Scan a single plugin.

    Args:
        plugin_name: Plugin name for display and cache key.
        plugin_path: Absolute path to the plugin directory.
        categories: List of category names to run (default: from profile).
        profile: Profile name for defaults (startup/install/update/manual).
        force: Force rescan (skip cache).
        use_cache: Whether to use the cache.

    Returns:
        ScanResult with all findings aggregated.
    """
    start_time = time.time()
    errors: list[str] = []

    # Resolve profile
    profiles = ScanProfile.profiles()
    scan_profile = profiles.get(profile, profiles["manual"])
    if scan_profile.name == "startup":
        # Pre-boot scans must remain predictable and fast. Deep external tools
        # are reserved for install/update/manual/CI profiles.
        kwargs.setdefault("use_semgrep", False)
        kwargs.setdefault("use_bandit", False)
        kwargs.setdefault("use_gitleaks", False)

    # Determine categories
    categories_to_run: list[str] = (
        list(categories) if categories is not None else list(scan_profile.categories)
    )
    categories_skipped: list[str] = []

    # Filter to implemented categories
    implemented = ScanCategory.implemented()
    actual_categories = []
    for cat in categories_to_run:
        if cat in implemented:
            actual_categories.append(cat)
        else:
            categories_skipped.append(cat)
    scan_context = _scan_context(
        plugin_name, actual_categories, scan_profile.name, kwargs
    )

    # Normalize path
    plugin_path = os.path.expanduser(plugin_path)
    if not os.path.isdir(plugin_path):
        return ScanResult(
            plugin_name=plugin_name,
            plugin_path=plugin_path,
            risk_level=RiskLevel.HIGH,
            categories_skipped=categories_skipped,
            scanner_version=_SCANNER_VERSION,
            errors=[f"Plugin path does not exist: {plugin_path}"],
        )

    # ── Cache lookup ────────────────────────────────────────────
    cache_enabled = use_cache and scan_profile.cache_ttl_hours > 0
    if cache_enabled and not force:
        ttl = scan_profile.cache_ttl_hours
        cached = cache_lookup(
            plugin_name,
            plugin_path,
            force=False,
            ttl_hours=ttl,
            scan_context=scan_context,
        )
        if cached is not None:
            # Rebuild findings from cached dict
            cached_findings = []
            cache_valid = True
            cached_risk = RiskLevel.HIGH
            for fd in cached.get("findings", []):
                try:
                    cached_findings.append(
                        Finding(
                            id=fd.get("id", ""),
                            plugin_name=fd.get("plugin_name", plugin_name),
                            category=fd.get("category", ""),
                            rule=fd.get("rule", ""),
                            severity=Severity(fd.get("severity", "warning")),
                            risk_level=RiskLevel(fd.get("risk_level", "none")),
                            file_path=fd.get("file_path", ""),
                            line=fd.get("line", 0),
                            message=fd.get("message", ""),
                            evidence=fd.get("evidence", ""),
                            remediation=fd.get("remediation", ""),
                            metadata=fd.get("metadata", {}),
                        )
                    )
                except (AttributeError, TypeError, ValueError, KeyError):
                    cache_valid = False
                    logger.warning(
                        "Ignoring corrupt scanner cache for plugin %s; running fresh scan",
                        plugin_name,
                    )
                    break

            if cache_valid:
                try:
                    cached_risk = RiskLevel(cached.get("risk_level", "none"))
                except ValueError:
                    cache_valid = False
                    logger.warning(
                        "Ignoring scanner cache with invalid risk for plugin %s; "
                        "running fresh scan",
                        plugin_name,
                    )

            if cache_valid:
                elapsed_ms = int((time.time() - start_time) * 1000)
                return ScanResult(
                    plugin_name=plugin_name,
                    plugin_path=plugin_path,
                    git_commit_hash=cached.get("git_commit_hash", ""),
                    file_tree_sha=cached.get("file_tree_sha", ""),
                    risk_level=cached_risk,
                    score=cached.get("score", 0.0),
                    findings=cached_findings,
                    categories_run=actual_categories,
                    categories_skipped=categories_skipped,
                    cache_hit=True,
                    scanned_at=cached.get("scanned_at", ""),
                    scanner_version=cached.get("scanner_version", ""),
                    duration_ms=elapsed_ms,
                )

    # ── Detect plugin type (skill vs code) ──────────────────────
    plugin_type = detect_plugin_type(plugin_path)
    # Pass to categories so they can apply skill-mode downgrades
    kwargs.setdefault("plugin_type", plugin_type)

    # ── Run categories ──────────────────────────────────────────
    all_findings: list[Finding] = []
    git_hash, tree_sha = compute_cache_key(plugin_path)

    for cat_name in actual_categories:
        runner = CATEGORY_RUNNERS.get(cat_name)
        if runner is None:
            categories_skipped.append(cat_name)
            continue
        try:
            runner_kwargs = {
                key: value
                for key, value in kwargs.items()
                if key in _CATEGORY_KWARGS.get(cat_name, set())
            }
            cat_findings = runner(
                plugin_name=plugin_name,
                plugin_path=plugin_path,
                **runner_kwargs,
            )
            all_findings.extend(cat_findings)
        except Exception as exc:
            errors.append(f"Category '{cat_name}' failed: {exc}")

    # ── Apply rule overrides from policy ────────────────────────
    all_findings, _ = apply_rule_overrides(all_findings, plugin_name)

    # ── Score and classify ──────────────────────────────────────
    score = _compute_score(all_findings)
    risk_level = _compute_risk_level(all_findings, score)
    if errors and risk_level.rank < RiskLevel.HIGH.rank:
        # A partial scan cannot safely authorize plugin execution.
        risk_level = RiskLevel.HIGH

    # ── Build result ────────────────────────────────────────────
    elapsed_ms = int((time.time() - start_time) * 1000)

    result = ScanResult(
        plugin_name=plugin_name,
        plugin_path=plugin_path,
        git_commit_hash=git_hash,
        file_tree_sha=tree_sha,
        risk_level=risk_level,
        score=score,
        findings=all_findings,
        categories_run=actual_categories,
        categories_skipped=categories_skipped,
        cache_hit=False,
        scanner_version=_SCANNER_VERSION,
        duration_ms=elapsed_ms,
        errors=errors,
    )

    # ── Store in cache ──────────────────────────────────────────
    if cache_enabled and not errors:
        ttl = scan_profile.cache_ttl_hours
        try:
            cache_store(
                plugin_name=plugin_name,
                plugin_path=plugin_path,
                scan_result=_scan_result_to_cache_dict(result),
                risk_level=result.risk_level.value,
                findings=[f.to_dict() for f in result.findings],
                score=result.score,
                ttl_hours=ttl,
                scan_context=scan_context,
            )
        except Exception as exc:
            errors.append(f"Failed to store cache: {exc}")
            result.errors = errors

    return result


def scan_plugins_dir(
    plugins_dir: str,
    *,
    categories: list[str] | None = None,
    profile: str = "startup",
    force: bool = False,
    **kwargs: Any,
) -> list[ScanResult]:
    """Scan all plugin directories under a parent path.

    Args:
        plugins_dir: Path containing plugin subdirectories.
        categories: Categories to run (default: from profile).
        profile: Scan profile name.
        force: Force rescan.

    Returns:
        List of ScanResult objects, one per plugin directory found.
    """
    plugins_dir = os.path.expanduser(plugins_dir)
    if not os.path.isdir(plugins_dir):
        return []

    results: list[ScanResult] = []
    try:
        entries = sorted(os.listdir(plugins_dir))
    except OSError:
        return []

    for entry in entries:
        entry_path = os.path.join(plugins_dir, entry)
        if not os.path.isdir(entry_path):
            continue
        # Skip non-plugin directories
        if entry.startswith(".") or entry.startswith("_"):
            continue

        result = scan_plugin(
            plugin_name=entry,
            plugin_path=entry_path,
            categories=categories,
            profile=profile,
            force=force,
            **kwargs,
        )
        results.append(result)

    return results


def scan_all(
    *,
    categories: list[str] | None = None,
    profile: str = "startup",
    force: bool = False,
    **kwargs: Any,
) -> list[ScanResult]:
    """Scan all known plugin locations."""
    results: list[ScanResult] = []

    # Standard plugin locations
    locations = [
        os.path.expanduser("~/.hermes/plugins"),
        os.path.expanduser("~/.hermes/skills"),
    ]

    for loc in locations:
        if os.path.isdir(loc):
            results.extend(
                scan_plugins_dir(
                    loc,
                    categories=categories,
                    profile=profile,
                    force=force,
                    **kwargs,
                )
            )

    return results
