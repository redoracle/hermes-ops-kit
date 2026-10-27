from unittest.mock import Mock

from hermes_ops_kit.cost_governor import budget
from hermes_ops_kit.commands import _handle_budget


def test_missing_spend_is_not_measured_zero():
    status = budget.evaluate_budget()
    assert status["budget_status"] == "unknown"
    assert status["daily_spend_usd"] is None
    assert status["monthly_percent"] is None
    assert not status["spend_available"]
    assert status["spend_source"] == "unavailable"
    assert status["actions"] == []


def test_explicit_zero_and_thresholds_still_work():
    assert budget.evaluate_budget(0, 0)["budget_status"] == "ok"
    assert budget.evaluate_budget(6, 0)["budget_status"] == "blocked"
    assert budget.evaluate_budget(6, None)["budget_status"] == "blocked"


def test_route_check_uses_single_config_snapshot(monkeypatch):
    load = Mock(return_value=budget.DEFAULT_BUDGET)
    monkeypatch.setattr(budget, "_load_budget", load)
    decision = budget.check_route_allowed("openai")
    assert decision.allowed and decision.status == "unknown"
    assert decision.warnings
    load.assert_called_once()


def test_cli_displays_missing_observation(capsys):
    assert _handle_budget(["status"]) == 0
    output = capsys.readouterr().out
    assert "UNKNOWN" in output and "unavailable" in output
    assert "$0.00" not in output
