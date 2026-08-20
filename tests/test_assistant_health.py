"""Tests for bounded assistant health aggregation."""

from __future__ import annotations

from hermes_ops_kit import usage_metrics_v2


def test_aggregate_assistant_checks_use_bounded_timeout(monkeypatch):
    """A slow optional assistant must not make status wait for its full timeout."""
    monkeypatch.setattr(usage_metrics_v2, "ASSISTANTS", {"slow": object()})
    observed: list[float | None] = []

    def fake_check(aid: str, timeout: float | None = None) -> dict:
        observed.append(timeout)
        return {"assistant": aid, "status": "online"}

    monkeypatch.setattr(usage_metrics_v2, "check_assistant", fake_check)

    assert usage_metrics_v2.check_all_assistants() == {
        "slow": {"assistant": "slow", "status": "online"}
    }
    assert observed == [5.0]
