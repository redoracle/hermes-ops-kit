"""scripts/preflight-scan.sh: it must fail loudly when it cannot look, and fail on a leak."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight-scan.sh"


def _has_pcre_grep() -> bool:
    for name in ("grep", "ggrep"):
        exe = shutil.which(name)
        if (
            exe
            and subprocess.run(
                [exe, "-qP", "x"], input="x", text=True, capture_output=True
            ).returncode
            == 0
        ):
            return True
    return False


needs_pcre = pytest.mark.skipif(
    not _has_pcre_grep(), reason="GNU grep with -P not installed"
)


@pytest.fixture()
def repo(tmp_path):
    """A throwaway git repository holding the script, so the real tree is never touched."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, root / "scripts" / "preflight-scan.sh")
    (root / "README.md").write_text("# demo\n\nNothing sensitive here.\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    return root


def _scan(root: Path, env_path: str | None = None, cwd: Path | None = None):
    env = {**os.environ, "PATH": env_path or os.environ["PATH"]}
    return subprocess.run(
        ["bash", str(root / "scripts" / "preflight-scan.sh")],
        cwd=cwd or root,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_without_pcre_grep_it_exits_2_instead_of_passing(repo, tmp_path):
    """BSD grep rejects -P; the script used to hide that and report PASS for every check."""
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "grep").write_text("#!/bin/sh\nexit 2\n")
    (fake / "grep").chmod(0o755)
    # system tools stay reachable, but the only grep and any ggrep on PATH are unusable
    done = _scan(repo, env_path=f"{fake}:/usr/bin:/bin")
    assert done.returncode == 2
    assert "GNU grep" in done.stdout


@needs_pcre
def test_a_clean_repository_passes_from_any_directory(repo, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    done = _scan(repo, cwd=elsewhere)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "All scans passed" in done.stdout


@needs_pcre
@pytest.mark.parametrize(
    "content,check",
    [
        ("ssh ubuntu@host.example", "SSH user"),
        ("server at 10.20.30.40", "non-local IP"),
        ("runs on my-oracle-vps", "cloud provider"),
        ("path /home/ubuntu/secret", "filesystem paths"),
        ("key sk-AbCdEfGhIjKlMnOpQrStUvWxYz012345", "API key"),
    ],
)
def test_each_kind_of_leak_fails_the_scan(repo, content, check):
    (repo / "notes.md").write_text(content + "\n")
    done = _scan(repo)
    assert done.returncode == 1 and check in done.stdout


@needs_pcre
def test_new_untracked_files_are_scanned_but_ignored_ones_are_not(repo):
    (repo / ".gitignore").write_text("local/\n")
    (repo / "local").mkdir()
    (repo / "local" / "private.md").write_text("ssh ubuntu@host.example\n")
    assert _scan(repo).returncode == 0  # ignored: not part of any commit
    (repo / "new.md").write_text(
        "ssh ubuntu@host.example\n"
    )  # untracked but not ignored
    assert _scan(repo).returncode == 1


@needs_pcre
def test_the_projects_own_repository_url_is_not_a_finding(repo):
    (repo / "docs.md").write_text(
        "git clone https://github.com/redoracle/hermes-ops-kit.git\n"
    )
    assert _scan(repo).returncode == 0


@needs_pcre
def test_this_repository_passes_its_own_scan():
    """The release script must not trip over the repository's own tests and docs."""
    repo = SCRIPT.parents[1]
    done = subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True, timeout=300
    )
    assert done.returncode == 0, done.stdout[-1500:]
