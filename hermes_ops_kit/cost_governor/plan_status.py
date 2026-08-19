"""Hermes Ops Kit — Nous plan / allowance status reader.

Reads the remaining Nous-plan credit allowance via Hermes core's canonical
``hermes_cli.nous_account.get_nous_portal_account_info`` client (the same
client that backs core's /credits, /usage, and /billing surfaces). The budget
engine uses this for allowance-aware gating: when the plan allowance is
exhausted, paid routes can be throttled or blocked in favour of the free tier.

This is integration, not re-implementation: ops-kit does NOT host its own
portal client. It calls core's (60s in-process cache) and degrades gracefully
to "unknown" when core is unavailable or has no OAuth token — in which case the
budget engine falls back to its existing USD-spend behaviour.

Portal base URL precedence (honoured by core): HERMES_PORTAL_BASE_URL →
NOUS_PORTAL_BASE_URL → stored auth-state → https://portal.nousresearch.com.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PlanAllowance:
    """Snapshot of the Nous plan allowance. ``available`` False = unknown."""

    available: bool
    total_usable_credits: Optional[float] = None
    subscription_credits_remaining: Optional[float] = None
    monthly_credits: Optional[float] = None
    plan: Optional[str] = None
    is_paid: Optional[bool] = None
    error: Optional[str] = None

    @property
    def exhausted(self) -> bool:
        """True when the plan has zero (or negative) usable credits.

        Unknown state (available=False) is never reported as exhausted — the
        budget engine must not block routes on a signal it could not read.
        """
        if not self.available:
            return False
        if self.total_usable_credits is not None:
            return self.total_usable_credits <= 0
        if self.subscription_credits_remaining is not None:
            return self.subscription_credits_remaining <= 0
        return False

    @property
    def usage_fraction(self) -> Optional[float]:
        """Fraction (0.0–1.0) of the monthly allowance consumed, or None."""
        if not self.available or not self.monthly_credits:
            return None
        remaining = (
            self.total_usable_credits
            if self.total_usable_credits is not None
            else self.subscription_credits_remaining
        )
        if remaining is None:
            return None
        used = self.monthly_credits - remaining
        try:
            return max(0.0, min(1.0, used / self.monthly_credits))
        except ZeroDivisionError:
            return None

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "total_usable_credits": self.total_usable_credits,
            "subscription_credits_remaining": self.subscription_credits_remaining,
            "monthly_credits": self.monthly_credits,
            "plan": self.plan,
            "is_paid": self.is_paid,
            "exhausted": self.exhausted,
            "usage_fraction": self.usage_fraction,
            "error": self.error,
        }


def get_plan_allowance(force_fresh: bool = False) -> PlanAllowance:
    """Read the Nous plan allowance via Hermes core's portal client.

    Never raises: returns ``PlanAllowance(available=False, ...)`` when core's
    client is not importable, has no token, or the call fails. Callers
    (budget.py) treat unknown as "do not gate on allowance".
    """
    try:  # lazy import — hermes_cli only present inside the Hermes process
        from hermes_cli.nous_account import (  # type: ignore[import-not-found]
            get_nous_portal_account_info,
        )
    except Exception as e:
        return PlanAllowance(
            available=False, error=f"nous_account unavailable: {e.__class__.__name__}"
        )

    try:
        info = get_nous_portal_account_info(force_fresh=force_fresh)
    except Exception as e:
        return PlanAllowance(
            available=False, error=f"portal read failed: {e.__class__.__name__}"
        )
    if info is None:
        return PlanAllowance(available=False, error="no account info (no token?)")

    try:
        sub = getattr(info, "subscription", None)
        paid = getattr(info, "paid_service_access_info", None)
        total = getattr(paid, "total_usable_credits", None) if paid else None
        remaining = getattr(sub, "credits_remaining", None) if sub else None
        monthly = getattr(sub, "monthly_credits", None) if sub else None
        plan = getattr(sub, "plan", None) if sub else None
        is_paid = getattr(info, "is_paid", None)

        available = any(v is not None for v in (total, remaining))
        return PlanAllowance(
            available=available,
            total_usable_credits=total,
            subscription_credits_remaining=remaining,
            monthly_credits=monthly,
            plan=plan,
            is_paid=is_paid,
            error=None if available else "no numeric allowance field",
        )
    except Exception as e:
        return PlanAllowance(
            available=False, error=f"parse failed: {e.__class__.__name__}"
        )
