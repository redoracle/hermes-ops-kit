"""Failures are reported with their cause and do not hide other results."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_ops_kit import commands, hermes_key_rotate as rotate


def test_rotate_all_reports_the_other_providers_when_one_raises(monkeypatch):
    names = list(rotate.PROVIDER_ROTATORS)
    assert len(names) > 1

    def fake(backend, provider, **kwargs):
        if provider == names[0]:
            raise RuntimeError(
                "GitHub App config missing token=sk-live-1234567890abcdef"
            )
        return {"ok": True, "provider": provider}

    monkeypatch.setattr(rotate, "cmd_rotate", fake)
    out = rotate.cmd_rotate_all_parallel(object(), dry_run=True, max_parallel=1)
    assert out["ok"] is False
    assert set(out["results"]) == set(names)  # every provider has a result
    failed = out["results"][names[0]]
    assert failed["ok"] is False and failed["error_type"] == "RuntimeError"
    assert "sk-live-1234567890abcdef" not in failed["error"]  # redacted
    assert all(out["results"][n]["ok"] for n in names[1:])


class _Backend:
    def __init__(self, admin):
        self.admin = admin

    def authenticate(self):
        pass

    def unlock(self):
        pass

    def sync(self):
        pass

    def get_secret(self, ref):
        if ref.endswith("/admin_key"):
            return SimpleNamespace(value="admin") if self.admin else None
        return SimpleNamespace(value="sk-old-key-0123456789")


class _Rotator:
    def __init__(self, revoked):
        self.revoked = revoked

    def revoke_key(self, ref, admin):
        return self.revoked


@pytest.mark.parametrize(
    "admin,revoked,needle",
    [
        (False, False, "no admin key is stored at hermes/openai/admin_key"),
        (True, False, "could not revoke the key"),
    ],
)
def test_revoke_only_says_why_it_did_not_revoke(monkeypatch, admin, revoked, needle):
    monkeypatch.setattr(
        rotate, "_get_rotator", lambda provider, backend: _Rotator(revoked)
    )
    out = rotate.cmd_emergency_compromise(_Backend(admin), "openai", revoke_only=True)
    assert out["ok"] is False and out["revoked"] is False
    assert out["manual_action_required"] is True
    assert needle in out["reason"]


def test_revoke_only_success_has_no_failure_reason(monkeypatch):
    monkeypatch.setattr(
        rotate, "_get_rotator", lambda provider, backend: _Rotator(True)
    )
    out = rotate.cmd_emergency_compromise(_Backend(True), "openai", revoke_only=True)
    assert out["ok"] is True and "reason" not in out


@pytest.mark.parametrize("sub", ["list", "audit", "tools", "risks", "export", "policy"])
def test_mcp_commands_report_a_corrupt_config_instead_of_a_traceback(
    sub, tmp_path, monkeypatch, capsys
):
    (tmp_path / "config.yaml").write_text(
        "model: [unclosed\n  : : bad", encoding="utf-8"
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    assert commands._handle_mcp([sub]) == 1
    out = capsys.readouterr().out
    assert out.startswith("ERROR: Cannot load")
    assert "Traceback" not in out
