"""Relative links and wikilinks in README.md and docs/ must point at something that exists.

A path that is right from inside a package directory is wrong from the repository root
(`skills/operator/` for `hermes_ops_kit/skills/operator/`), and a renamed doc leaves its old
wikilink behind. Neither shows up in the command tests.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

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
    listing = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "docs/*.md"],
        capture_output=True,
        env={"PATH": os.environ.get("PATH", "")},
        check=False,
    )
    tracked = [root / n for n in listing.stdout.decode().split("\0") if n]
    return (
        sorted(tracked)
        if listing.returncode == 0 and tracked
        else sorted((root / "docs").glob("*.md"))
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
