"""Hermes Ops Kit — budget engine: allowance gating + new provider classes."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cost_governor import budget  # pyright: ignore[reportMissingImports]
import cost_governor.plan_status as ps  # pyright: ignore[reportMissingImports]
from cost_governor.plan_status import PlanAllowance  # pyright: ignore[reportMissingImports]

# Use the committed config/budget.yaml (not the user's ~/.hermes/ops-kit/budget.yaml,
# which takes precedence at runtime and may not list newly added providers) so
# provider classification is deterministic in tests.
COMMITTED_BUDGET = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "budget.yaml"
)


@pytest.fixture(autouse=True)
def _committed_budget(monkeypatch):
    monkeypatch.setattr(budget, "BUDGET_CONFIG_PATHS", [COMMITTED_BUDGET])


def test_provider_classes_include_new_providers():
    assert budget._provider_class("fireworks") == "paid_low"
    assert budget._provider_class("deepinfra") == "paid_low"
    assert budget._provider_class("deepseek") == "paid_low"
    assert budget._provider_class("gemini") == "free_or_included"
    assert budget._provider_class("openai") == "paid_standard"
    assert budget._provider_class("anthropic") == "paid_premium"


def test_evaluate_budget_has_plan_allowance():
    r = budget.evaluate_budget()
    assert "plan_allowance" in r
    pa = r["plan_allowance"]
    assert pa["available"] is False  # core hermes_cli not importable in test env
    assert pa["exhausted"] is False


def _exhausted(monkeypatch):
    monkeypatch.setattr(
        ps,
        "get_plan_allowance",
        lambda force_fresh=False: PlanAllowance(
            available=True, total_usable_credits=0.0, monthly_credits=100.0
        ),
    )


def test_premium_blocked_when_allowance_exhausted(monkeypatch):
    _exhausted(monkeypatch)
    d = budget.check_route_allowed("anthropic")  # paid_premium
    assert not d.allowed
    assert d.recommended_provider == "gemini"
    assert d.requires_override
    assert any("allowance" in w.lower() for w in d.warnings)


def test_standard_blocked_when_allowance_exhausted(monkeypatch):
    _exhausted(monkeypatch)
    d = budget.check_route_allowed("openai")  # paid_standard
    assert not d.allowed
    assert d.recommended_provider == "gemini"


def test_paid_low_not_blocked_when_allowance_exhausted(monkeypatch):
    # paid_low providers use direct API keys (not Nous credits) → stay allowed.
    _exhausted(monkeypatch)
    d = budget.check_route_allowed("fireworks")
    assert d.allowed
    d2 = budget.check_route_allowed("deepinfra")
    assert d2.allowed


def test_free_not_blocked_when_allowance_exhausted(monkeypatch):
    _exhausted(monkeypatch)
    d = budget.check_route_allowed("gemini")
    assert d.allowed


def test_not_blocked_when_allowance_unknown(monkeypatch):
    # available=False → not exhausted → normal behaviour (spend at 0 → ok).
    monkeypatch.setattr(
        ps,
        "get_plan_allowance",
        lambda force_fresh=False: PlanAllowance(available=False, error="no token"),
    )
    d = budget.check_route_allowed("anthropic")
    assert d.allowed


def test_budget_yaml_nested_budgets_honored(tmp_path, monkeypatch):
    # config/budget.yaml nests daily_usd/monthly_usd under `budgets:`; the engine
    # must read the nested value, not silently fall back to DEFAULT (regression
    # test for the shallow-merge config-ignored bug).
    cfg = tmp_path / "budget.yaml"
    cfg.write_text(
        "version: 1\n"
        "budgets:\n"
        "  daily_usd: 50.0\n"
        "  monthly_usd: 500.0\n"
        "thresholds:\n"
        "  warn_percent: 70\n"
        "  throttle_percent: 85\n"
        "  restrict_percent: 95\n"
        "  block_percent: 100\n"
        "enforcement_mode: advisory\n"
        "provider_classes:\n"
        "  free_or_included: [github, gemini]\n"
        "  paid_low: [deepseek, fireworks, deepinfra]\n"
        "  paid_standard: [openai]\n"
        "  paid_premium: [anthropic]\n"
    )
    monkeypatch.setattr(budget, "BUDGET_CONFIG_PATHS", [str(cfg)])
    r = budget.evaluate_budget()
    assert r["daily_budget_usd"] == 50.0
    assert r["monthly_budget_usd"] == 500.0


def test_block_classes_read_from_config(tmp_path, monkeypatch):
    # block_classes is read from budget.yaml actions.block.block_classes, not
    # hardcoded — an operator adding paid_low to block_classes is honored.
    cfg = tmp_path / "budget.yaml"
    cfg.write_text(
        "version: 1\n"
        "budgets:\n  daily_usd: 5.0\n  monthly_usd: 100.0\n"
        "thresholds:\n  warn_percent: 70\n  throttle_percent: 85\n"
        "  restrict_percent: 95\n  block_percent: 100\n"
        "enforcement_mode: advisory\n"
        "provider_classes:\n"
        "  free_or_included: [github, gemini]\n"
        "  paid_low: [deepseek, fireworks]\n"
        "  paid_standard: [openai]\n"
        "  paid_premium: [anthropic]\n"
        "actions:\n  block:\n    block_classes: [paid_premium, paid_standard, paid_low]\n"
    )
    monkeypatch.setattr(budget, "BUDGET_CONFIG_PATHS", [str(cfg)])
    r = budget.evaluate_budget()
    assert "paid_low" in r["block_classes"]
    # With allowance exhausted + paid_low in block_classes, fireworks is blocked.
    monkeypatch.setattr(
        ps,
        "get_plan_allowance",
        lambda force_fresh=False: PlanAllowance(
            available=True, total_usable_credits=0.0, monthly_credits=100.0
        ),
    )
    d = budget.check_route_allowed("fireworks")
    assert not d.allowed


def test_block_classes_empty_disables_blocking(tmp_path, monkeypatch):
    # An explicit empty block_classes must be honored (disable class-based
    # blocking), not silently overridden by the default — `[] or [default]`
    # would wrongly fall back to the default.
    cfg = tmp_path / "budget.yaml"
    cfg.write_text(
        "version: 1\n"
        "budgets:\n  daily_usd: 5.0\n  monthly_usd: 100.0\n"
        "thresholds:\n  warn_percent: 70\n  throttle_percent: 85\n"
        "  restrict_percent: 95\n  block_percent: 100\n"
        "enforcement_mode: advisory\n"
        "provider_classes:\n"
        "  free_or_included: [github, gemini]\n"
        "  paid_low: [deepseek]\n"
        "  paid_standard: [openai]\n"
        "  paid_premium: [anthropic]\n"
        "actions:\n  block:\n    block_classes: []\n"
    )
    monkeypatch.setattr(budget, "BUDGET_CONFIG_PATHS", [str(cfg)])
    r = budget.evaluate_budget()
    assert r["block_classes"] == []  # empty honored, not overridden
    # Allowance exhausted + empty block_classes → even paid_premium is allowed.
    monkeypatch.setattr(
        ps, "get_plan_allowance",
        lambda force_fresh=False: PlanAllowance(available=True, total_usable_credits=0.0, monthly_credits=100.0),
    )
    assert budget.check_route_allowed("anthropic").allowed
