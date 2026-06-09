"""Hermes Ops Kit — Plugin Scanner: Finding Models & Risk Classification.

Defines the canonical data model for scan findings, risk levels,
and recommended actions. Used by all scan categories and the orchestrator.
"""

from __future__ import annotations

import enum
import hashlib
import time
from dataclasses import dataclass, field, asdict
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────


class RiskLevel(str, enum.Enum):
    """Risk classification for a finding or a plugin overall."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_score(cls, score: float) -> RiskLevel:
        """Map an aggregated score to a risk level."""
        if score >= 50:
            return cls.CRITICAL
        if score >= 25:
            return cls.HIGH
        if score >= 10:
            return cls.MEDIUM
        if score >= 1:
            return cls.LOW
        return cls.NONE

    @property
    def rank(self) -> int:
        """Numeric rank for comparison/sorting."""
        return _RISK_RANK[self]

    @property
    def blocks_plugin(self) -> bool:
        """Whether this risk level blocks plugin execution by default."""
        return self in (RiskLevel.CRITICAL, RiskLevel.HIGH)

    @property
    def disables_plugin(self) -> bool:
        """Whether this risk level disables the plugin by default."""
        return self in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)


_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.NONE: 0,
    RiskLevel.LOW: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.HIGH: 3,
    RiskLevel.CRITICAL: 4,
}


class Action(str, enum.Enum):
    """Action recommended or taken for a finding."""

    ALLOW = "allow"
    WARN = "warn"
    DISABLE = "disable"
    BLOCK = "block"


class ScanCategory(str, enum.Enum):
    """Scan categories. Only secrets + policy are MVP."""

    SECRETS = "secrets"
    POLICY = "policy"
    CODE = "code"  # Phase 2
    DEPENDENCIES = "dependencies"  # Phase 2
    BEHAVIOR = "behavior"  # Phase 3
    REPUTATION = "reputation"  # Phase 3

    @classmethod
    def mvp_categories(cls) -> list[ScanCategory]:
        return [cls.SECRETS, cls.POLICY]

    @classmethod
    def implemented(cls) -> set[ScanCategory]:
        """Categories that are actually implemented (not just scaffolded)."""
        return {cls.SECRETS, cls.POLICY}


class Severity(str, enum.Enum):
    """Severity of an individual finding."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def multiplier(self) -> float:
        """Weight multiplier for aggregated scoring."""
        return _SEVERITY_MULTIPLIER[self]


_SEVERITY_MULTIPLIER: dict[Severity, float] = {
    Severity.ERROR: 1.0,
    Severity.WARNING: 0.6,
    Severity.INFO: 0.3,
}


# ── Data Models ──────────────────────────────────────────────────────


@dataclass
class Finding:
    """A single security finding from any scan category."""

    id: str  # Unique ID: "<plugin>:<category>:<rule>:<hash>"
    plugin_name: str
    category: str  # ScanCategory value
    rule: str  # Rule identifier, e.g. "shell-execution", "hardcoded-secret"
    severity: Severity
    risk_level: RiskLevel
    file_path: str = ""  # Relative to plugin root
    line: int = 0  # 1-based, 0 if not applicable
    message: str = ""
    evidence: str = ""  # Redacted evidence — never raw secrets
    remediation: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = self._generate_id()

    def _generate_id(self) -> str:
        """Deterministic finding ID for dedup and approval matching."""
        raw = f"{self.plugin_name}:{self.category}:{self.rule}:{self.file_path}:{self.line}"
        short_hash = hashlib.sha256(raw.encode()).hexdigest()[:12]
        return f"{self.plugin_name}:{self.category}:{self.rule}:{short_hash}"

    @property
    def action(self) -> Action:
        """Default action based on risk level."""
        if self.risk_level == RiskLevel.CRITICAL:
            return Action.BLOCK
        if self.risk_level == RiskLevel.HIGH:
            return Action.BLOCK
        if self.risk_level == RiskLevel.MEDIUM:
            return Action.DISABLE
        if self.risk_level == RiskLevel.LOW:
            return Action.WARN
        return Action.ALLOW

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (JSON-safe)."""
        d = asdict(self)
        d["severity"] = self.severity.value
        d["risk_level"] = self.risk_level.value
        d["category"] = self.category
        return d


@dataclass
class ScanResult:
    """Aggregated result of scanning one plugin."""

    plugin_name: str
    plugin_path: str
    git_commit_hash: str = ""
    file_tree_sha: str = ""
    risk_level: RiskLevel = RiskLevel.NONE
    score: float = 0.0
    findings: list[Finding] = field(default_factory=list)
    categories_run: list[str] = field(default_factory=list)
    categories_skipped: list[str] = field(default_factory=list)
    cache_hit: bool = False
    scanned_at: str = ""  # ISO 8601
    scanner_version: str = ""
    duration_ms: int = 0
    errors: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.scanned_at:
            self.scanned_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    @property
    def is_clean(self) -> bool:
        return self.risk_level in (RiskLevel.NONE, RiskLevel.LOW)

    @property
    def is_blocked(self) -> bool:
        return self.risk_level.blocks_plugin

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_name": self.plugin_name,
            "plugin_path": self.plugin_path,
            "git_commit_hash": self.git_commit_hash,
            "file_tree_sha": self.file_tree_sha,
            "risk_level": self.risk_level.value,
            "score": self.score,
            "findings": [f.to_dict() for f in self.findings],
            "categories_run": self.categories_run,
            "categories_skipped": self.categories_skipped,
            "cache_hit": self.cache_hit,
            "scanned_at": self.scanned_at,
            "scanner_version": self.scanner_version,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
        }


@dataclass
class ScanProfile:
    """Configuration for a scan profile (startup, install, update, manual)."""

    name: str
    description: str
    categories: list[str]
    timeout_seconds: int = 60
    parallel: bool = True
    block_on: list[str] = field(default_factory=lambda: ["critical", "high"])
    cache_ttl_hours: int = 168  # 7 days

    @classmethod
    def profiles(cls) -> dict[str, ScanProfile]:
        """Built-in scan profiles."""
        return {
            "startup": cls(
                name="startup",
                description="Run at Hermes startup — fast, essential",
                categories=["secrets", "policy"],
                timeout_seconds=12,
                parallel=True,
                block_on=["critical", "high"],
                cache_ttl_hours=168,
            ),
            "install": cls(
                name="install",
                description="Run on first plugin install — deeper scan",
                categories=["secrets", "policy"],
                timeout_seconds=60,
                parallel=True,
                block_on=["critical", "high"],
                cache_ttl_hours=0,  # Always scan fresh on install
            ),
            "update": cls(
                name="update",
                description="Run on plugin update — force rescan",
                categories=["secrets", "policy"],
                timeout_seconds=60,
                parallel=False,
                block_on=["critical", "high"],
                cache_ttl_hours=0,
            ),
            "manual": cls(
                name="manual",
                description="Full manual scan on demand",
                categories=["secrets", "policy"],
                timeout_seconds=120,
                parallel=False,
                block_on=["critical"],
                cache_ttl_hours=0,
            ),
            "ci": cls(
                name="ci",
                description="CI/CD pipeline — offline-only, fast",
                categories=["secrets", "policy"],
                timeout_seconds=60,
                parallel=True,
                block_on=["critical"],
                cache_ttl_hours=0,
            ),
        }
