"""Hermes Ops Kit — Budget Engine.

Reads budget.yaml, calculates thresholds, evaluates spend status.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

BUDGET_CONFIG_PATHS = [
    os.path.expanduser("~/.hermes/ops-kit/budget.yaml"),
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config",
        "budget.yaml",
    ),
]

DEFAULT_BUDGET = {
    "daily_usd": 5.00,
    "monthly_usd": 100.00,
    "thresholds": {
        "warn_percent": 70,
        "throttle_percent": 85,
        "restrict_percent": 95,
        "block_percent": 100,
    },
    "enforcement_mode": "advisory",
    "provider_classes": {
        "free_or_included": ["github", "gemini"],
        "paid_low": ["deepseek", "fireworks", "deepinfra"],
        "paid_standard": ["openai"],
        "paid_premium": ["anthropic"],
    },
}


@dataclass
class BudgetDecision:
    allowed: bool
    status: str
    reason: str
    recommended_provider: str | None = None
    recommended_model: str | None = None
    requires_override: bool = False
    warnings: list[str] = field(default_factory=list)


def _load_budget() -> dict[str, Any]:
    for p in BUDGET_CONFIG_PATHS:
        if os.path.exists(p):
            try:
                import yaml as _yaml  # pyright: ignore[reportMissingImports,reportMissingModuleSource]

                with open(p) as f:
                    cfg = _yaml.safe_load(f) or {}
                return {**DEFAULT_BUDGET, **cfg}
            except Exception:
                pass
    return dict(DEFAULT_BUDGET)


def _provider_class(provider: str) -> str:
    cfg = _load_budget()
    for cls_name, providers in cfg.get("provider_classes", {}).items():
        if provider in providers:
            return cls_name
    return "unknown"


def evaluate_budget(daily_spend: float = 0, monthly_spend: float = 0) -> dict[str, Any]:
    """Evaluate current budget status."""
    cfg = _load_budget()
    thresholds = cfg.get("thresholds", {})
    # budget.yaml nests these under `budgets:`; fall back to top-level then DEFAULT.
    _b = cfg.get("budgets", {})
    daily_budget = _b.get("daily_usd", cfg.get("daily_usd", 5.0))
    monthly_budget = _b.get("monthly_usd", cfg.get("monthly_usd", 100.0))

    daily_pct = round(daily_spend / daily_budget * 100, 1) if daily_budget else 0
    monthly_pct = (
        round(monthly_spend / monthly_budget * 100, 1) if monthly_budget else 0
    )

    max_pct = max(daily_pct, monthly_pct)
    if max_pct >= thresholds.get("block_percent", 100):
        status = "blocked"
    elif max_pct >= thresholds.get("restrict_percent", 95):
        status = "restrict"
    elif max_pct >= thresholds.get("throttle_percent", 85):
        status = "throttle"
    elif max_pct >= thresholds.get("warn_percent", 70):
        status = "warn"
    else:
        status = "ok"

    actions = []
    if status in ("warn", "throttle", "restrict", "blocked"):
        actions.append("warn")
    if status in ("throttle", "restrict", "blocked"):
        actions.append("reroute_to_cheaper")
    if status == "blocked":
        actions.append("block_paid_providers")

    # ── Nous plan allowance (best-effort; integrates core hermes_cli.nous_account) ──
    try:
        from cost_governor.plan_status import get_plan_allowance

        allowance = get_plan_allowance().as_dict()
    except Exception:
        allowance = {"available": False, "exhausted": False}

    # Classes to block when the budget is exhausted — single source of truth
    # from config/budget.yaml actions.block.block_classes (default premium+standard).
    block_classes = (
        cfg.get("actions", {}).get("block", {}).get("block_classes")
        or ["paid_premium", "paid_standard"]
    )

    return {
        "ok": status != "blocked",
        "budget_status": status,
        "daily_spend_usd": daily_spend,
        "daily_budget_usd": daily_budget,
        "daily_percent": daily_pct,
        "monthly_spend_usd": monthly_spend,
        "monthly_budget_usd": monthly_budget,
        "monthly_percent": monthly_pct,
        "enforcement_mode": cfg.get("enforcement_mode", "advisory"),
        "actions": actions,
        "blocked_providers": [],
        "preferred_providers": cfg.get("provider_classes", {}).get(
            "free_or_included", []
        )
        + cfg.get("provider_classes", {}).get("paid_low", []),
        "plan_allowance": allowance,
        "block_classes": block_classes,
    }


def check_route_allowed(
    provider: str, _model: str = "", _purpose: str = ""
) -> BudgetDecision:
    """Check if a route is allowed under current budget policy."""
    status = evaluate_budget()
    pclass = _provider_class(provider)
    block_classes = status.get("block_classes") or ["paid_premium", "paid_standard"]
    mode = status.get("enforcement_mode", "advisory")

    if mode == "report_only":
        return BudgetDecision(
            True, status["budget_status"], f"report_only mode: {provider} allowed"
        )

    # ── Allowance-aware gating: block the configured block_classes when the Nous plan is exhausted ──
    # Uses the same block_classes as the spend-based block below (single source of
    # truth: config/budget.yaml actions.block.block_classes). Default = premium+standard,
    # so paid_low (direct API keys, not Nous credits) stays allowed.
    plan = status.get("plan_allowance") or {}
    if plan.get("exhausted") and pclass in block_classes:
        return BudgetDecision(
            False,
            "blocked",
            f"{provider} blocked: Nous plan allowance exhausted",
            recommended_provider="gemini",
            requires_override=True,
            warnings=[
                "Nous plan allowance exhausted — use the free tier or /billing to top up"
            ],
        )

    if (
        pclass in block_classes
        and status["budget_status"] == "blocked"
    ):
        return BudgetDecision(
            False,
            "blocked",
            f"{provider} blocked: daily budget exceeded",
            recommended_provider="gemini",
            requires_override=True,
            warnings=["Budget blocked — use hermes-ops-kit budget allow"],
        )

    # Stricter premium-only warn under restrict/throttle (paid_standard is NOT
    # warned here — intentional). block_classes above governs the hard block.
    if pclass in ("paid_premium",) and status["budget_status"] in (
        "restrict",
        "throttle",
    ):
        return BudgetDecision(
            True,
            status["budget_status"],
            f"{provider} allowed with warning",
            warnings=[
                f"{provider} is premium-paid, budget at {status['daily_percent']}%"
            ],
        )

    if status["budget_status"] in ("warn",) and mode == "advisory":
        return BudgetDecision(
            True,
            status["budget_status"],
            f"{provider} allowed (advisory mode)",
            warnings=[
                f"Budget at {status['daily_percent']}% — consider cheaper routes"
            ],
        )

    return BudgetDecision(True, status["budget_status"], f"{provider} allowed")
