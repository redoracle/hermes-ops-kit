"""The documented hermes-key-rotate flows, run end to end against a read-only fake ``bw``.

This checks the calls ops-kit makes and what it prints and writes. It is not evidence of
live vault behavior.
"""

from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPENAI_KEY = "sk-proj-abc123-fake-openai-key-1234"
DEEPSEEK_KEY = "sk-abc123testsecretnotreal-deepseek"


@pytest.fixture()
def vault(tmp_path):
    home = tmp_path / "home" / ".hermes"
    home.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shutil.copy(ROOT / "tests" / "fakes" / "fake_bw.py", bin_dir / "bw")
    (bin_dir / "bw").chmod(0o755)
    items = [
        {
            "id": "i1",
            "name": "Hermes/OpenAI/API_KEY",
            "revisionDate": "2026-01-01T00:00:00Z",
            "login": {"password": OPENAI_KEY},
        },
        {
            "id": "i2",
            "name": "Hermes/DeepSeek/API_KEY",
            "revisionDate": "2026-01-02T00:00:00Z",
            "login": {"password": DEEPSEEK_KEY},
        },
    ]
    (tmp_path / "items.json").write_text(json.dumps(items))
    env_file = home / ".env"
    env_file.write_text(
        "HERMES_SECRET_BACKEND=vaultwarden\nVAULTWARDEN_SERVER_URL=https://vault.test\n"
        "HERMES_AUTH_MODE=bitwarden_cli_session\nBW_SESSION=fake-session\n"
    )
    env_file.chmod(0o600)
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "HERMES_HOME": str(home),
        "FAKE_BW_ITEMS": str(tmp_path / "items.json"),
        "PYTHONPATH": str(ROOT),
    }

    def run(*args: str, stdin: str = ""):
        done = subprocess.run(
            [sys.executable, "-P", "-m", "hermes_ops_kit.hermes_key_rotate", *args],
            capture_output=True,
            text=True,
            input=stdin,
            env=env,
            cwd=tmp_path,
            timeout=60,
        )
        return done.returncode, done.stdout, done.stderr

    run.home = home
    run.env_file = env_file
    return run


def _json(out: str) -> dict:
    return json.loads(out)


def test_healthcheck(vault):
    code, out, _ = vault("--healthcheck")
    data = _json(out)
    assert code == 0 and data["ok"] and data["unlocked"] and data["tls_configured"]


def test_list_refs(vault):
    code, out, _ = vault("--list-refs")
    assert code == 0 and _json(out)["refs"] == [
        "hermes/deepseek/api_key",
        "hermes/openai/api_key",
    ]


def test_status_never_prints_secret_values(vault):
    code, out, err = vault("--status")
    assert code == 0 and _json(out)["ref_count"] == 2
    assert OPENAI_KEY not in out + err and DEEPSEEK_KEY not in out + err


def test_doctor_secrets_counts_the_vault_refs(vault):
    code, out, _ = vault("--doctor-secrets")
    backend = _json(out)["SECRET BACKEND"]
    assert backend["status"] == "ready" and backend["refs"] == 2


def test_render_env_writes_a_private_generated_file(vault):
    code, out, err = vault("--render-env")
    generated = vault.home / ".env.generated"
    assert code == 0 and _json(out)["rendered"] is True
    assert stat.S_IMODE(generated.stat().st_mode) == 0o600
    from hermes_ops_kit.env.loader import parse_env_file

    assert parse_env_file(str(generated))["OPENAI_API_KEY"] == OPENAI_KEY
    assert OPENAI_KEY not in out + err


def test_diff_reports_sync_state(vault):
    vault("--render-env")
    code, out, _ = vault("diff")
    assert code == 0 and _json(out)["vault_refs"] == 2


def test_backup_vault_writes_metadata_only(vault):
    code, out, _ = vault("backup-vault")
    backup = Path(_json(out)["output"])
    assert code == 0 and backup.is_relative_to(vault.home)
    assert OPENAI_KEY not in backup.read_text()


def test_seed_from_env_dry_run_writes_nothing(vault):
    with open(vault.env_file, "a") as f:
        f.write("OPENAI_API_KEY=sk-proj-abc123-new-fake-0000\n")
    code, out, _ = vault("seed-from-env", "--dry-run")
    data = _json(out)
    assert code == 0 and data["dry_run"] is True and data["failed"] == 0


def test_rotate_dry_run_changes_nothing(vault):
    code, out, _ = vault("rotate", "--dry-run", "--provider", "openai")
    assert code == 0 and _json(out)["dry_run"] is True


def test_a_failing_write_never_echoes_the_new_key(vault):
    """The fake vault refuses writes (rc 64): the candidate key must not leak into output."""
    candidate = "sk-proj-abc123-candidate-fake-0000"
    before = vault.env_file.read_text()
    code, out, err = vault(
        "rotate",
        "--provider",
        "openai",
        "--manual-new-key-stdin",
        stdin=candidate + "\n",
    )
    assert code != 0
    assert candidate not in out + err
    assert vault.env_file.read_text() == before


def test_migrate_with_everything_already_stored_changes_nothing(vault):
    """The stub vault already holds the key that .env also names: nothing to seed."""
    with open(vault.env_file, "a") as f:
        f.write("OPENAI_API_KEY=sk-proj-abc123-new-fake-0000\n")
    code, out, _ = vault("migrate")
    data = _json(out)
    assert code == 0 and data["ok"] is True
    assert "nothing to migrate" in data["message"]
    assert data["already_stored"] == ["OPENAI_API_KEY"]


def test_rotate_all_providers_runs_one_at_a_time_by_default(vault):
    """README: `rotate --provider all` is "all providers sequentially"; it used to fail with
    `Unknown provider: all` because only `--parallel` handled `all`."""
    code, out, _ = vault("rotate", "--provider", "all", "--dry-run")
    data = _json(out)
    assert "Unknown provider" not in out
    assert set(data["results"]) >= {"openai", "anthropic", "deepseek"}


def test_rotate_all_providers_in_parallel_still_works(vault):
    code, out, _ = vault("rotate", "--provider", "all", "--parallel", "--dry-run")
    assert "results" in _json(out) and "Unknown provider" not in out


def test_one_manual_key_cannot_be_used_for_every_provider(vault):
    for extra in ([], ["--parallel"]):
        code, out, _ = vault(
            "rotate",
            "--provider",
            "all",
            "--manual-new-key-stdin",
            *extra,
            stdin="sk-proj-abc123-one-key\n",
        )
        assert code == 1 and "single --provider" in _json(out)["error"]
