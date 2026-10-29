"""Relative links and wikilinks in README.md and docs/ must point at something that exists.

A path that is right from inside a package directory is wrong from the repository root
(`skills/operator/` for `hermes_ops_kit/skills/operator/`), and a renamed doc leaves its old
wikilink behind. Neither shows up in the command tests.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
INLINE_CODE = re.compile(r"`[^`\n]*`")
MD_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
EXTERNAL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|#|/)", re.I)


def _prose(path: Path) -> str:
    """The file's text without code, where `[x](y)` and `[[x]]` are examples, not links."""
    text = path.read_text(encoding="utf-8")
    return INLINE_CODE.sub("", FENCE.sub("", text))


def _docs(root: Path) -> list[Path]:
    """`docs/*.md` as a clean checkout has them: tracked files only, so a git-ignored local
    note cannot make a link look alive here and dead in CI."""
    docs_dir = root / "docs"
    try:
        listing = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--", ":(glob)docs/*.md"],
            capture_output=True,
            env={
                k: os.environ[k]
                for k in ("PATH", "HOME", "SYSTEMROOT")
                if k in os.environ
            },
            check=False,
        )
    except OSError:  # no git binary: an sdist or a minimal image
        listing = None
    tracked = (
        [root / n for n in listing.stdout.decode().split("\0") if n]
        if listing is not None and listing.returncode == 0
        else []
    )
    # the glob matches top-level notes only, like the fallback below
    return sorted(p for p in tracked if p.parent == docs_dir) or sorted(
        docs_dir.glob("*.md")
    )


def broken_links(root: Path) -> list[str]:
    """`file: target` for every relative markdown link or docs wikilink that resolves to nothing."""
    docs = _docs(root)
    files = [root / "README.md", *docs]
    stems = {f.stem.lower() for f in docs}
    problems = []
    for path in files:
        text = _prose(path)
        for target in MD_LINK.findall(text):
            if EXTERNAL.match(target):
                continue
            if not (path.parent / unquote(target.split("#", 1)[0])).exists():
                problems.append(f"{path.relative_to(root)}: {target}")
        if path.parent.name != "docs":
            continue  # README is rendered by GitHub, which has no wikilinks
        for target in WIKILINK.findall(text):
            name = target.strip()
            if name.lower() in stems or (root / f"{name}.md").exists():
                continue
            problems.append(f"{path.relative_to(root)}: [[{name}]]")
    return problems


def test_the_repository_docs_have_no_dead_links():
    assert broken_links(ROOT) == []


def test_the_checker_catches_what_it_is_for(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "pkg" / "skills").mkdir(parents=True)
    (tmp_path / "docs" / "real doc.md").write_text("# real\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "[ok](pkg/skills/) [dead](skills/) [web](https://example.test/x_(y)) [top](#top)\n"
        "`[example](nowhere/)`\n"
        "```\n[fenced](nowhere/)\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "a.md").write_text(
        "[[real doc]] [[docs/real doc|alias]] [[Old Name]] [[real doc#Heading]]\n",
        encoding="utf-8",
    )
    assert broken_links(tmp_path) == ["README.md: skills/", "docs/a.md: [[Old Name]]"]


def test_docs_are_the_tracked_top_level_notes_only(tmp_path):
    """A git-ignored local note must not count (it passed locally and failed in CI once), and
    neither may a nested one: the wikilink check only knows top-level stems."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    env = {k: os.environ[k] for k in ("PATH", "HOME", "SYSTEMROOT") if k in os.environ}
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    for name in ("a.md", "sub/b.md", "local-only.md"):
        (tmp_path / "docs" / name).write_text("# note\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("docs/local-only.md\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."]):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True,
            capture_output=True,
            env=env,
        )
    assert [p.name for p in _docs(tmp_path)] == ["a.md"]


def test_without_a_git_binary_the_listing_falls_back_to_the_glob(tmp_path, monkeypatch):
    (tmp_path / "docs").mkdir()
    for name in ("a.md", "local.md"):
        (tmp_path / "docs" / name).write_text("# note\n", encoding="utf-8")

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)
    assert [p.name for p in _docs(tmp_path)] == ["a.md", "local.md"]
