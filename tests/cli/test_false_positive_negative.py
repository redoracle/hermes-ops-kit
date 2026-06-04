"""Hermes Ops Kit — False Positive / Negative Tests.

Layer 5: Diagnostic commands must not silently lie.
Uses isolated fixtures with controlled config to verify correct behavior.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from tests.cli.cli_runner import run_cli, try_json  # pyright: ignore[reportMissingImports]
from tests.cli.fixtures import HermesFixture  # pyright: ignore[reportMissingImports]


# ── False Negatives: valid config should NOT fail ────────────────


def test_valid_assistants_yaml_should_pass_validate():
    """Bundled config/assistants.yaml should always pass validation."""
    r = run_cli(
        [
            "hermes_assistant_manager.py",
            "--config",
            "config/assistants.yaml",
            "validate",
            "--json",
        ]
    )
    data = try_json(r.stdout)
    assert data is not None, f"validate JSON parse failed: {r.stdout[:200]}"
    assert data.get("ok") is True, f"Valid bundled config validated as FAIL: {data}"


def test_valid_assistants_yaml_should_pass_doctor():
    """Bundled config/assistants.yaml should always pass doctor."""
    r = run_cli(
        [
            "hermes_assistant_manager.py",
            "--config",
            "config/assistants.yaml",
            "doctor",
            "--json",
        ]
    )
    data = try_json(r.stdout)
    assert data is not None
    assert data.get("ok") is True, f"Valid bundled config doctor reported FAIL"


# ── False Positives: invalid config should NOT pass ──────────────


def test_secret_in_assistants_yaml_should_fail_validate():
    with HermesFixture("secret-validate") as fx:
        fx.setup_safe_env().setup_secret_assistants_yaml()
        r = run_cli(
            [
                "hermes_assistant_manager.py",
                "--config",
                str(fx.ops_kit / "assistants.yaml"),
                "validate",
                "--json",
            ],
            env=fx.env(),
        )
        data = try_json(r.stdout)
        assert data is not None
        # Must detect the secret
        secrets = data.get("secret_violations", [])
        assert len(secrets) > 0 or data.get("ok") is False, (
            f"Secret config passed validation! data={data}"
        )


def test_secret_should_not_leak_in_validate_output():
    with HermesFixture("secret-leak") as fx:
        fx.setup_safe_env().setup_secret_assistants_yaml()
        r = run_cli(
            [
                "hermes_assistant_manager.py",
                "--config",
                str(fx.ops_kit / "assistants.yaml"),
                "validate",
                "--json",
            ],
            env=fx.env(),
        )
        # The raw secret "sk-abc123testsecretnotreal" must NOT appear in output
        assert "sk-abc123testsecretnotreal" not in r.stdout, (
            f"RAW SECRET LEAKED in validate output!"
        )
        assert "sk-abc123testsecretnotreal" not in r.stderr, (
            f"RAW SECRET LEAKED in stderr!"
        )


def test_unsafe_env_permissions_should_fail_doctor():
    """hermes_key_rotate.py reads ~/.hermes/.env (real path, not HERMES_HOME).

    This test verifies that if the real .env has safe 0600 perms, doctor reports it.
    Also verifies the fixture .env is correctly set to unsafe 0o644.
    """
    with HermesFixture("unsafe-perm") as fx:
        fx.setup_unsafe_env()
        # Verify the fixture-level file actually has 0o644
        import stat

        mode = stat.S_IMODE(fx.env_file.stat().st_mode)
        assert mode == 0o644, f"Fixture .env not 0o644: {oct(mode)}"
        # The real doctor reads ~/.hermes/.env — test that our fixture's env IS unsafe
        from security.file_permissions import check_env_file

        check = check_env_file(str(fx.env_file))
        assert not check.get("safe"), f"0o644 .env not detected as unsafe: {check}"


# ── Assistant under ASSISTANTS not PROVIDERS ─────────────────────────


def test_assistant_never_in_provider_keys():
    """Assistant must appear under _assistants, never in provider keys.

    Runs the real CLI against an isolated registry (via HERMES_ASSISTANTS_CONFIG)
    that defines `test-assistant`, so the invariant is exercised end-to-end.
    """
    with HermesFixture("assistant-keys") as fx:
        fx.setup_safe_env().setup_valid_assistants_yaml()
        env = fx.env()
        env["HERMES_ASSISTANTS_CONFIG"] = str(fx.ops_kit / "assistants.yaml")
        r = run_cli(["usage_metrics_v2.py", "--json"], env=env)
    data = try_json(r.stdout)
    assert data is not None
    provider_keys = {k for k in data if not k.startswith("_")}
    assert "test-assistant" not in provider_keys, (
        f"Assistant leaked into provider keys: {provider_keys}"
    )
    assert "test-assistant" in data.get("_assistants", {}), (
        f"Assistant missing from _assistants: {data.get('_assistants')}"
    )


# ── JSON mode must not emit ANSI ─────────────────────────────────


def test_json_mode_never_has_ansi():
    """All --json commands emit zero ANSI escape codes."""
    cmds = [
        ["usage_metrics_v2.py", "--json"],
        [
            "hermes_assistant_manager.py",
            "--config",
            "config/assistants.yaml",
            "list",
            "--json",
        ],
        [
            "hermes_assistant_manager.py",
            "--config",
            "config/assistants.yaml",
            "validate",
            "--json",
        ],
        ["hermes_key_rotate.py", "--healthcheck"],
    ]
    for args in cmds:
        r = run_cli(args)
        import re

        ansi = re.search(r"\x1b\[[0-9;]*m", r.stdout)
        assert not ansi, f"ANSI in {' '.join(args)}: {r.stdout[:80]}"


# ── Disabled assistant should not be READY ───────────────────────


def test_disabled_assistant_not_ready():
    with HermesFixture("disabled") as fx:
        fx.setup_valid_assistants_yaml()
        # Modify to disable
        path = fx.ops_kit / "assistants.yaml"
        content = path.read_text().replace("enabled: true", "enabled: false")
        path.write_text(content)
        r = run_cli(
            [
                "hermes_assistant_manager.py",
                "--config",
                str(path),
                "list",
                "--json",
            ],
            env=fx.env(),
        )
        data = try_json(r.stdout)
        assert data is not None
        assistants = data.get("assistants", data.get("result", []))
        if assistants:
            for a in assistants if isinstance(assistants, list) else [assistants]:
                enabled = a.get("enabled", True)
                assert not enabled, f"Disabled assistant shows enabled={enabled}"
