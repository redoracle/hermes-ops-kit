"""Hermes Ops Kit — plan_status (Nous allowance reader) tests."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hermes_ops_kit.cost_governor.plan_status import PlanAllowance, get_plan_allowance  # pyright: ignore[reportMissingImports]


def test_unknown_not_exhausted():
    p = PlanAllowance(available=False, error="no token")
    assert not p.exhausted
    assert p.usage_fraction is None


def test_exhausted_when_zero_total():
    p = PlanAllowance(available=True, total_usable_credits=0.0, monthly_credits=100.0)
    assert p.exhausted
    assert p.usage_fraction == 1.0


def test_not_exhausted_when_positive():
    p = PlanAllowance(available=True, total_usable_credits=50.0, monthly_credits=100.0)
    assert not p.exhausted
    assert p.usage_fraction == 0.5


def test_exhausted_via_remaining_only():
    p = PlanAllowance(
        available=True, subscription_credits_remaining=0.0, monthly_credits=100.0
    )
    assert p.exhausted


def test_usage_fraction_clamped():
    # remaining > monthly (rollover) → usage_fraction clamped to 0.0, not negative
    p = PlanAllowance(available=True, total_usable_credits=150.0, monthly_credits=100.0)
    assert p.usage_fraction == 0.0
    assert not p.exhausted


def test_get_plan_allowance_graceful_when_no_core():
    # In the ops-kit test env hermes_cli is not importable → available=False, never raises.
    p = get_plan_allowance()
    assert p.available is False
    assert not p.exhausted  # unknown → must NOT be reported as exhausted
    assert p.error


def test_as_dict_shape():
    p = PlanAllowance(
        available=True, total_usable_credits=25.0, monthly_credits=100.0, plan="Ultra"
    )
    d = p.as_dict()
    assert d["available"] is True
    assert d["exhausted"] is False
    assert d["usage_fraction"] == 0.75
    assert d["plan"] == "Ultra"
