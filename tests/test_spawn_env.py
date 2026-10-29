"""Every subprocess spawn passes an explicit environment (F2, S5.1).

A spawn without ``env=`` inherits the launch profile's whole environment, which
can hand its credentials to work done for another profile or to scanner tools
that read untrusted plugin content.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from hermes_ops_kit import _subprocess

PACKAGE = Path(__file__).resolve().parents[1] / "hermes_ops_kit"
SPAWNERS = {"run", "Popen", "check_output", "check_call", "call"}
SECRET = "synthetic-launch-profile-secret"


def _spawn_calls(tree: ast.AST):
    """Yield every call that starts a process, resolving ``import subprocess as x``
    and ``from subprocess import run as r``."""
    module_names: set[str] = set()
    function_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module_names |= {
                a.asname or a.name for a in node.names if a.name == "subprocess"
            }
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            function_names |= {
                a.asname or a.name for a in node.names if a.name in SPAWNERS
            }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in SPAWNERS
            and isinstance(func.value, ast.Name)
            and func.value.id in module_names
        ) or (isinstance(func, ast.Name) and func.id in function_names):
            yield node


def _copies_launch_environment(value: ast.AST) -> bool:
    """True if *value* hands the launch environment on: ``os.environ`` itself,
    ``dict(os.environ)``, ``os.environ.copy()`` or ``{**os.environ, ...}``.

    Reading single variables (``os.environ.get("PATH")``, ``os.environ["X"]``) is fine.
    """
    parents = {
        child: node for node in ast.walk(value) for child in ast.iter_child_nodes(node)
    }
    for node in ast.walk(value):
        if ast.unparse(node) != "os.environ":
            continue
        parent = parents.get(node)
        reads_one_variable = (
            isinstance(parent, ast.Attribute) and parent.attr == "get"
        ) or (isinstance(parent, ast.Subscript) and parent.value is node)
        if not reads_one_variable:
            return True
    return False


# Files that build the environment on purpose.
ENVIRONMENT_BUILDERS = {"_subprocess.py"}


def test_every_process_spawn_has_an_explicit_environment_that_is_not_a_copy():
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = str(path.relative_to(PACKAGE))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _spawn_calls(tree):
            env = next((kw.value for kw in call.keywords if kw.arg == "env"), None)
            if env is None:
                offenders.append(f"{rel}:{call.lineno}: no env=")
            elif rel not in ENVIRONMENT_BUILDERS and _copies_launch_environment(env):
                offenders.append(f"{rel}:{call.lineno}: env copies os.environ")
            if any(
                kw.arg == "shell" and getattr(kw.value, "value", None) is True
                for kw in call.keywords
            ):
                offenders.append(f"{rel}:{call.lineno}: shell=True")
    assert offenders == []


def test_no_other_process_spawning_api_is_used():
    banned = {
        "system",
        "popen",
        "execv",
        "execl",
        "execvp",
        "execlp",
        "spawnv",
        "spawnl",
    }
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and (
                    (node.func.value.id == "os" and node.func.attr in banned)
                    or (
                        node.func.value.id == "asyncio"
                        and node.func.attr.startswith("create_subprocess")
                    )
                )
            ):
                offenders.append(
                    f"{path.relative_to(PACKAGE)}:{node.lineno}: {ast.unparse(node.func)}"
                )
    assert offenders == []


class _Captured(list):
    commands: list[list[str]]


@pytest.fixture
def standalone_python(tmp_path):
    """An existing interpreter outside every Hermes-owned location."""
    (tmp_path / "venv/bin").mkdir(parents=True)
    python = tmp_path / "venv/bin/python"
    python.write_text("#!/bin/sh\n")
    return str(python)


@pytest.fixture
def spawned(monkeypatch):
    """Capture the ``env`` of every ``subprocess.run`` made during a test."""
    envs = _Captured()
    commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        envs.append(kwargs.get("env"))
        commands.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"results": []}', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("GH_TOKEN", SECRET)
    envs.commands = commands  # type: ignore[attr-defined]
    return envs


def _assert_credential_free(env):
    assert env is not None
    assert SECRET not in "".join(env.values())
    assert set(env) <= set(_subprocess._MINIMAL_KEYS)


def test_scanner_tools_run_with_a_credential_free_environment(spawned, tmp_path):
    from hermes_ops_kit.security.plugin_scanner.categories import policy, secrets

    plugin = tmp_path / "plugin"
    plugin.mkdir()
    (plugin / "x.py").write_text("x = 1\n")
    policy._semgrep_available()
    policy._run_semgrep(str(plugin), [])
    policy._bandit_available()
    policy._run_bandit(str(plugin))
    secrets._gitleaks_available()
    secrets._run_gitleaks(str(plugin))
    assert {cmd[0] for cmd in spawned.commands} >= {"semgrep", "bandit", "gitleaks"}
    for env in spawned:
        _assert_credential_free(env)


def test_reading_a_plugins_git_head_uses_a_credential_free_environment(
    spawned, tmp_path
):
    from hermes_ops_kit.security.plugin_scanner.cache import compute_git_commit_hash

    compute_git_commit_hash(str(tmp_path))
    assert len(spawned) == 1
    _assert_credential_free(spawned[0])


def test_installer_gets_the_profile_environment_without_credentials(
    spawned, fake_core, profiles, standalone_python
):
    from hermes_ops_kit.install_reconciler.installer_adapter import run_install

    home = profiles.use("a")
    fake_core.child_env_result = {
        "PATH": "/usr/bin"
    }  # core scrubbed the launch secrets
    run_install(standalone_python, "/src", False, installer="pip")
    assert fake_core.child_env_calls == [
        {"target_home": str(home), "inherit_credentials": False}
    ]
    assert spawned[0]["HERMES_HOME"] == str(home)
    assert SECRET not in "".join(spawned[0].values())


def test_standalone_installer_keeps_the_parent_environment_and_adds_home(
    spawned, monkeypatch, tmp_path, standalone_python
):
    import sys

    from hermes_ops_kit import ops_config_io
    from hermes_ops_kit.install_reconciler.installer_adapter import run_install

    monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    run_install(standalone_python, "/src", False, installer="pip")
    assert spawned[0]["HERMES_HOME"] == str(tmp_path)
    assert spawned[0]["OPENAI_API_KEY"] == SECRET  # standalone behavior is unchanged


def test_updater_git_calls_use_the_profile_environment(spawned, fake_core, profiles):
    from hermes_ops_kit.install_reconciler import updater

    profiles.use("a")
    updater._git("/src", "status", "--porcelain")
    assert fake_core.child_env_calls[-1]["inherit_credentials"] is False
    assert spawned[0]["HERMES_HOME"] == str(profiles.path("a"))


def test_a_core_refusal_stops_the_spawn(
    spawned, fake_core, profiles, monkeypatch, standalone_python
):
    from hermes_ops_kit.install_reconciler.installer_adapter import run_install

    def refuse(*args, **kwargs):
        raise fake_core.UnscopedSecretError("", "no scope under multiplexing")

    monkeypatch.setattr(fake_core.local_module, "served_profile_child_env", refuse)
    profiles.use("a")
    # a refusal is a result, not a crash, and nothing is launched
    result = run_install(standalone_python, "/src", False, installer="pip")
    assert result.refused and not result.ok
    assert spawned == []


# --- the guards themselves must catch what they claim to --------------------------------


def _offences(source: str) -> list[str]:
    found = []
    for call in _spawn_calls(ast.parse(source)):
        env = next((kw.value for kw in call.keywords if kw.arg == "env"), None)
        if env is None:
            found.append("no env")
        elif _copies_launch_environment(env):
            found.append("copies env")
    return found


@pytest.mark.parametrize(
    "source,expected",
    [
        ("import subprocess\nsubprocess.run(['x'])", ["no env"]),
        ("import subprocess as _sp\n_sp.run(['x'])", ["no env"]),
        ("from subprocess import run\nrun(['x'])", ["no env"]),
        ("from subprocess import Popen as P\nP(['x'])", ["no env"]),
        (
            "import subprocess, os\nsubprocess.run(['x'], env=os.environ)",
            ["copies env"],
        ),
        (
            "import subprocess, os\nsubprocess.run(['x'], env={**os.environ, 'A': '1'})",
            ["copies env"],
        ),
        (
            "import subprocess, os\nsubprocess.run(['x'], env=dict(os.environ))",
            ["copies env"],
        ),
        (
            "import subprocess, os\nsubprocess.run(['x'], env=os.environ.copy())",
            ["copies env"],
        ),
        (
            "import subprocess, os\nsubprocess.run(['x'], env={'PATH': os.environ.get('PATH', '')})",
            [],
        ),
        ("import subprocess\nsubprocess.run(['x'], env=child_env())", []),
        (
            "import subprocess\nsubprocess.getoutput('x')",
            [],
        ),  # not a spawner we allow elsewhere
    ],
)
def test_the_spawn_guard_catches_aliases_and_environment_copies(source, expected):
    assert _offences(source) == expected


def test_every_workflow_declares_least_privilege_permissions():
    import yaml

    workflows = sorted((PACKAGE.parent / ".github" / "workflows").glob("*.yml"))
    assert workflows
    missing = []
    for path in workflows:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = document.get("jobs", {})
        if "permissions" not in document and not all(
            "permissions" in job for job in jobs.values()
        ):
            missing.append(path.name)
    assert missing == []
