"""Keep collection-time constants and subprocesses away from operational state."""

import os
import sys
import tempfile
import types
from pathlib import Path

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_home: the test may resolve the real ~/.hermes (it only inspects the path)",
    )
    config._ops_test_home = tempfile.TemporaryDirectory(prefix="ops-kit-tests-")
    config._ops_previous_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = config._ops_test_home.name


def pytest_unconfigure(config):
    if not hasattr(config, "_ops_test_home"):
        return
    previous = config._ops_previous_home
    if previous is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = previous
    config._ops_test_home.cleanup()


class Profiles:
    """Two (or more) Hermes profile homes behind a stand-in for core's get_hermes_home().

    ``use(name)`` switches the active profile for subsequent ``ops_config_io.home()``
    calls, the way a routed profile does inside one Hermes process.
    """

    def __init__(self, root: Path):
        self.root = root
        self.active = "default"

    def path(self, name: str) -> Path:
        home = self.root / name
        home.mkdir(parents=True, exist_ok=True)
        return home

    def use(self, name: str) -> Path:
        self.active = name
        return self.path(name)


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    """Per-profile homes with no explicit override and a fake core resolving the active one."""
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", ops_config_io._IMPORT_TIME_HOME)
    state = Profiles(tmp_path / "profiles")
    state.use("default")
    core = types.ModuleType("hermes_constants")
    setattr(core, "get_hermes_home", lambda: state.path(state.active))
    monkeypatch.setitem(sys.modules, "hermes_constants", core)
    return state


class FakeCore:
    """Stand-ins for the core pieces ops-kit consults when embedded in Hermes.

    ``scope`` is the bound profile secret scope (None = unbound); ``multiplex``
    mirrors ``is_multiplex_active``; ``core_secrets`` backs core's ``get_secret``.
    ``child_env_calls`` records every ``served_profile_child_env`` call.
    """

    def __init__(self):
        self.scope = None
        self.multiplex = False
        self.core_secrets: dict[str, str] = {}
        self.child_env_calls: list[dict] = []
        self.child_env_result: dict[str, str] | None = None


@pytest.fixture
def fake_core(profiles, monkeypatch):
    """Embedded-in-Hermes stand-ins on top of the ``profiles`` fixture."""
    state = FakeCore()

    class UnscopedSecretError(RuntimeError):
        def __init__(self, secret_name="", developer_detail=""):
            super().__init__(f"unscoped:{secret_name}")
            self.developer_detail = developer_detail

    secret_scope = types.ModuleType("agent.secret_scope")
    setattr(secret_scope, "UnscopedSecretError", UnscopedSecretError)
    setattr(secret_scope, "current_secret_scope", lambda: state.scope)
    setattr(secret_scope, "is_multiplex_active", lambda: state.multiplex)
    setattr(
        secret_scope,
        "get_secret",
        lambda name, default=None: state.core_secrets.get(name, default),
    )

    local = types.ModuleType("tools.environments.local")

    def served_profile_child_env(
        base=None, *, target_home=None, inherit_credentials=False
    ):
        state.child_env_calls.append(
            {"target_home": target_home, "inherit_credentials": inherit_credentials}
        )
        if state.child_env_result is not None:
            return dict(state.child_env_result)
        return {"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(target_home)}

    setattr(local, "served_profile_child_env", served_profile_child_env)
    monkeypatch.setitem(sys.modules, "agent.secret_scope", secret_scope)
    monkeypatch.setitem(sys.modules, "tools.environments.local", local)
    state.UnscopedSecretError = UnscopedSecretError
    state.local_module = local
    return state


@pytest.fixture(autouse=True)
def _tests_never_resolve_the_real_hermes_home(request, monkeypatch):
    """Fail any test whose ``ops_config_io.home()`` lands on the real ``~/.hermes``.

    ``home()`` resolves at call time, so a test that clears ``HERMES_HOME`` (for
    example ``patch.dict(os.environ, ..., clear=True)``) would silently read or
    write the developer's real profile. Pin the home explicitly instead.
    """
    if request.node.get_closest_marker("real_home"):
        return
    from hermes_ops_kit import ops_config_io

    real = os.path.realpath(os.path.expanduser("~/.hermes"))
    original = ops_config_io.home

    def guarded() -> str:
        path = original()
        if os.path.realpath(path) == real:
            raise AssertionError(
                "test resolved the real ~/.hermes; set HERMES_HOME or "
                "ops_config_io.HERMES_HOME to a temporary directory"
            )
        return path

    monkeypatch.setattr(ops_config_io, "home", guarded)
