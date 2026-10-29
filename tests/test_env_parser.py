"""Env-file parsing contract (F9).

One parser (python-dotenv, interpolation off) defines what a value is. Every public
loader must agree on parsed file values while keeping its own environment contract:
``load_dotenv`` overrides, ``load_env_setdefault`` never clobbers, the dict loaders
never touch ``os.environ``.
"""

from __future__ import annotations

import os

import pytest

from hermes_ops_kit.env import env_loader, loader

# (id, file body, expected parse). ``${...}`` is never expanded: expansion would read
# ambient credentials of whichever profile launched the process.
PARSE_CASES = [
    ("plain", "A=1\n", {"A": "1"}),
    ("spaces-around-equals", "K = v\n", {"K": "v"}),
    ("export-prefix", "export A=1\n", {"A": "1"}),
    ("unquoted-trailing-comment", "C=unquoted # trailing\n", {"C": "unquoted"}),
    ("hash-without-space-is-value", "H=ab#cd\n", {"H": "ab#cd"}),
    ("dq-with-hash-and-comment", 'B="x # y" # c\n', {"B": "x # y"}),
    ("sq-with-trailing-comment", "M='p'  # c\n", {"M": "p"}),
    ("escaped-double-quote", 'D="a\\"b"\n', {"D": 'a"b'}),
    ("escaped-single-quote", "C='it\\'s'\n", {"C": "it's"}),
    ("backslash-n-in-double-quotes", 'N="l1\\nl2"\n', {"N": "l1\nl2"}),
    ("multiline-quoted", 'ML="l1\nl2"\n', {"ML": "l1\nl2"}),
    ("empty-value", "E=\n", {"E": ""}),
    ("empty-quoted", 'EQ=""\n', {"EQ": ""}),
    ("bare-key-without-equals-ignored", "G\nA=1\n", {"A": "1"}),
    (
        "interpolation-default-not-expanded",
        "A=1\nH2=${A:-d}\n",
        {"A": "1", "H2": "${A:-d}"},
    ),
    ("interpolation-ambient-not-expanded", "H3=${HOME}\n", {"H3": "${HOME}"}),
    ("dollar-literal", "P=pa$$word\n", {"P": "pa$$word"}),
    ("equals-inside-value", "U=https://h/?a=b&c=d\n", {"U": "https://h/?a=b&c=d"}),
    ("dash-in-key", "MY-KEY=v\n", {"MY-KEY": "v"}),
    ("duplicate-last-wins", "A=1\nA=2\n", {"A": "2"}),
    ("apostrophe-in-bare-value", "Q=it's\n", {"Q": "it's"}),
    ("crlf", "A=1\r\nB=2\r\n", {"A": "1", "B": "2"}),
    ("comment-and-blank-lines", "# c\n\nA=1\n", {"A": "1"}),
    ("leading-whitespace", "  A=1\n", {"A": "1"}),
    ("quoted-trailing-space-kept", 'S="a "\n', {"S": "a "}),
    ("malformed-trailing-garbage-dropped", 'I="a"b\nA=1\n', {"A": "1"}),
]


def _write(path, body):
    path.write_text(body, encoding="utf-8", newline="")
    return str(path)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "hermes"
    h.mkdir()
    monkeypatch.setattr(loader.ops_config_io, "HERMES_HOME", str(h))
    return h


@pytest.fixture
def environ(monkeypatch):
    """A minimal private ``os.environ`` so loader side effects cannot leak or collide."""
    env = {"PATH": os.environ.get("PATH", "")}
    monkeypatch.setattr(os, "environ", env)
    return env


@pytest.mark.parametrize(
    "body,expected", [c[1:] for c in PARSE_CASES], ids=[c[0] for c in PARSE_CASES]
)
def test_parse_env_file_semantics(tmp_path, body, expected):
    assert loader.parse_env_file(_write(tmp_path / ".env", body)) == expected


def test_missing_file_parses_to_empty(tmp_path):
    assert loader.parse_env_file(str(tmp_path / "absent")) == {}


@pytest.mark.parametrize(
    "body,expected", [c[1:] for c in PARSE_CASES], ids=[c[0] for c in PARSE_CASES]
)
def test_every_public_loader_agrees_on_file_values(home, environ, body, expected):
    path = _write(home / ".env", body)
    baseline = dict(environ)

    def loaded():
        return {
            k: v
            for k, v in environ.items()
            if baseline.get(k) != v or k not in baseline
        }

    assert loader.parse_env_file(path) == expected
    assert env_loader.load_dotenv(path) == expected
    assert loader.load_env_dict(str(home)) == expected
    assert environ == baseline  # the dict loaders never touch the environment

    loader.load_env_setdefault(path)
    assert loaded() == expected

    environ.clear()
    environ.update(baseline)
    loader._ENV_LOADED = False
    try:
        loader.load_dotenv()
    finally:
        loader._ENV_LOADED = True
    assert loaded() == expected


def test_no_loader_expands_variables_from_the_ambient_environment(home, environ):
    environ["AMBIENT_SECRET"] = "leaked-value"
    _write(home / ".env", "X=${AMBIENT_SECRET}\nY=pre-${AMBIENT_SECRET}\n")
    expected = {"X": "${AMBIENT_SECRET}", "Y": "pre-${AMBIENT_SECRET}"}

    assert loader.parse_env_file(str(home / ".env")) == expected
    assert loader.load_env_dict(str(home)) == expected

    loader._ENV_LOADED = False
    try:
        loader.load_dotenv()
    finally:
        loader._ENV_LOADED = True
    assert environ["X"] == "${AMBIENT_SECRET}"
    assert "leaked-value" not in environ["Y"]


def test_generated_file_wins_over_bootstrap_but_keeps_bootstrap_only_keys(home):
    _write(home / ".env", "A=1\nBOOT_ONLY=keep\n")
    _write(home / ".env.generated", "A=2\nGEN_ONLY=g\n")
    expected = {"A": "2", "BOOT_ONLY": "keep", "GEN_ONLY": "g"}
    assert loader.load_env_dict(str(home)) == expected


def test_load_dotenv_overrides_existing_environment(home, environ):
    environ["A"] = "from-env"
    _write(home / ".env", "A=1\n")
    _write(home / ".env.generated", "A=2\n")
    loader._ENV_LOADED = False
    try:
        loader.load_dotenv()
    finally:
        loader._ENV_LOADED = True
    assert environ["A"] == "2"


def test_setdefault_never_clobbers_real_env_and_generated_beats_bootstrap(
    home, environ
):
    environ["REAL"] = "real"
    environ.pop("A", None)
    _write(home / ".env", "REAL=file\nA=1\n")
    _write(home / ".env.generated", "A=2\n")
    loader.load_env_setdefault()
    assert environ["REAL"] == "real"
    assert environ["A"] == "2"


def test_dict_loaders_do_not_touch_the_environment(home, environ):
    _write(home / ".env", "ONLY_IN_FILE=1\n")
    before = dict(environ)
    loader.load_env_dict(str(home))
    loader.parse_env_file(str(home / ".env"))
    env_loader.load_dotenv(str(home / ".env"))
    assert environ == before


def test_deprecated_shim_load_hermes_env_merges_and_sets_environ(home, environ):
    _write(
        home / ".env",
        "HERMES_SECRET_BACKEND=vaultwarden\nVAULTWARDEN_SERVER_URL=u\nA=1\n",
    )
    _write(home / ".env.generated", "A=2\n")
    merged = env_loader.load_hermes_env()
    assert merged["A"] == "2"
    assert environ["A"] == "2"


def _switching_core(monkeypatch, current):
    import sys
    import types

    core = types.ModuleType("hermes_constants")
    setattr(core, "get_hermes_home", lambda: current["home"])
    monkeypatch.setitem(sys.modules, "hermes_constants", core)


def test_env_loaders_follow_the_active_profile_at_call_time(tmp_path, monkeypatch):
    """Two profiles, one process: every call resolves the profile active right now."""
    from hermes_ops_kit import ops_config_io

    profile_a, profile_b = tmp_path / "a", tmp_path / "b"
    for profile, value in ((profile_a, "from-a"), (profile_b, "from-b")):
        profile.mkdir()
        (profile / ".env").write_text(f"WHO={value}\n")
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", ops_config_io._IMPORT_TIME_HOME)
    current = {"home": profile_a}
    _switching_core(monkeypatch, current)

    assert loader.load_env_dict() == {"WHO": "from-a"}
    assert env_loader.get_hermes_env_path() == str(profile_a / ".env")
    current["home"] = profile_b
    assert loader.load_env_dict() == {"WHO": "from-b"}
    assert env_loader.get_hermes_env_path() == str(profile_b / ".env")
    assert env_loader.get_generated_env_path() == str(profile_b / ".env.generated")
