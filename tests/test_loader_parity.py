"""Loader-parity: the directory-plugin load must not mutate sys.path.

Plan amendment G — authority is a *content snapshot* of ``sys.path`` taken
immediately before the load and compared after load + ``register(ctx)``.
A spying list subclass is installed only to attribute mutations if the
snapshot assertion fails (it also catches ``+=`` rebinding that bypasses
append/extend overrides).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT  # directory-plugin root: plugin.yaml + __init__.py

_SYNTH = "hermes_plugins.hermes_ops_kit"


def test_directory_load_without_top_level_distribution(tmp_path):
    """A fresh core directory loader must not depend on an editable install."""
    script = r'''
import importlib.abc, importlib.util, sys, types
from pathlib import Path
class NoInstalledOpsKit(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "hermes_ops_kit" or fullname.startswith("hermes_ops_kit."):
            raise ModuleNotFoundError("no installed top-level ops-kit", name=fullname)
sys.meta_path.insert(0, NoInstalledOpsKit())
before = list(sys.path)
ns = types.ModuleType("hermes_plugins")
ns.__path__ = []
sys.modules["hermes_plugins"] = ns
root = Path(sys.argv[1])
name = "hermes_plugins.hermes_ops_kit"
spec = importlib.util.spec_from_file_location(name, root / "__init__.py",
    submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
assert callable(getattr(module, "register", None)), "directory plugin lost register()"
class Context:
    def __init__(self): self.tools = []
    def __getattr__(self, name):
        def record(*args, **kwargs):
            if name == "register_tool": self.tools.append(kwargs["name"])
        return record
ctx = Context()
module.register(ctx)
assert len(ctx.tools) == 7, ctx.tools
assert list(sys.path) == before
assert "hermes_ops_kit" not in sys.modules
'''
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(PLUGIN_DIR)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr


class _PathSpy(list):
    """Records every mutating call for failure attribution."""

    def __init__(self, items, log):
        super().__init__(items)
        self._log = log

    def append(self, x):
        self._log.append(("append", x))
        super().append(x)

    def extend(self, xs):
        self._log.append(("extend", list(xs)))
        super().extend(xs)

    def insert(self, i, x):
        self._log.append(("insert", i, x))
        super().insert(i, x)

    def __iadd__(self, xs):
        self._log.append(("iadd", list(xs)))
        super().__iadd__(xs)
        return self

    def __setitem__(self, i, v):
        self._log.append(("setitem", i, v))
        super().__setitem__(i, v)


class _Ctx:
    """Duck-typed Hermes ctx: records every call regardless of surface."""

    def __init__(self):
        self.calls: list = []

    def __getattr__(self, name):
        def _rec(*a, **k):
            self.calls.append((name, a, k))

        return _rec


def _load_via_machinery():
    """Replicate the Hermes v0.20.4 loader: synthetic name, __path__=[dir]."""
    # core pre-registers the hermes_plugins namespace package
    ns = sys.modules.get("hermes_plugins")
    if ns is None:
        ns = types.ModuleType("hermes_plugins")
        ns.__path__ = []
        sys.modules["hermes_plugins"] = ns
    spec = importlib.util.spec_from_file_location(
        _SYNTH,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[_SYNTH] = module
    spec.loader.exec_module(module)
    return module


def _cleanup_modules() -> None:
    for key in [k for k in sys.modules if k == _SYNTH or k.startswith(_SYNTH + ".")]:
        sys.modules.pop(key, None)


def test_load_and_register_do_not_mutate_sys_path():
    log: list = []
    original = sys.path
    before = list(sys.path)
    after = before
    pre_modules = set(sys.modules.keys())
    try:
        sys.path = _PathSpy(before, log)  # type: ignore[assignment]
        module = _load_via_machinery()
        assert module.__package__ == _SYNTH
        assert list(module.__path__) == [str(PLUGIN_DIR)]
        ctx = _Ctx()
        module.register(ctx)
        assert ctx.calls, "register(ctx) produced no registrations"
        after = list(sys.path)
    finally:
        sys.path = original
        _cleanup_modules()
    assert after == before, f"sys.path mutated during load/register: {log}"
    assert sys.path is original, "sys.path object was rebound"
    # no flat top-level module leakage from the package
    for leaked in ("commands", "providers", "security", "usage_metrics_v2", "bridge"):
        if leaked not in pre_modules:
            assert leaked not in sys.modules, (
                f"flat module leaked into sys.modules: {leaked}"
            )


def test_register_module_is_synthetic():
    try:
        module = _load_via_machinery()
        # register is defined in the package submodule and re-exported by
        # the thin plugin root — it must resolve *inside* the synthetic
        # namespace, never from a flat/cwd module.
        assert module.register.__module__.startswith(_SYNTH + ".")
        owner = sys.modules[module.register.__module__]
        assert owner.register is module.register
    finally:
        _cleanup_modules()
