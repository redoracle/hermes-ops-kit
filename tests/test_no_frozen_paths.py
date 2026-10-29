"""Guard: no kit module freezes the Hermes home at import time (F2).

Internal code must resolve paths through ``ops_config_io.home()`` (or a function built
on it). Only the documented compatibility aliases, of the form ``NAME = local_func()``,
may evaluate a path at import. The check is on the syntax tree, so renamed or
re-imported names are caught too.
"""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "hermes_ops_kit"
OPS_CONFIG_IO = PACKAGE / "ops_config_io.py"
FROZEN = {"HERMES_HOME", "HERMES_CONFIG", "OPS_KIT_DIR"}


def _modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        if path != OPS_CONFIG_IO:
            yield path, ast.parse(path.read_text(encoding="utf-8"))


def _imports_from_ops_config_io(tree: ast.AST) -> dict[str, str]:
    """local name -> imported name, for ``from ..ops_config_io import X as Y``."""
    names: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[-1] == "ops_config_io"
        ):
            for alias in node.names:
                names[alias.asname or alias.name] = alias.name
    return names


def test_no_module_reads_the_frozen_home_constants():
    offenders = []
    for path, tree in _modules():
        rel = path.relative_to(PACKAGE)
        for local, imported in _imports_from_ops_config_io(tree).items():
            if imported in FROZEN:
                offenders.append(f"{rel}: imports {imported} as {local}")
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in FROZEN
                and isinstance(node.value, ast.Name)
                and node.value.id == "ops_config_io"
            ):
                offenders.append(f"{rel}:{node.lineno}: ops_config_io.{node.attr}")
    assert offenders == []


def _mentions_ops_config_io(value: ast.AST, imported: dict[str, str]) -> bool:
    for node in ast.walk(value):
        if isinstance(node, ast.Name) and (
            node.id == "ops_config_io" or node.id in imported
        ):
            return True
    return False


def test_module_level_paths_are_only_deprecated_function_aliases():
    """A module-level assignment built from ops_config_io must be ``NAME = local_func()``."""
    offenders = []
    for path, tree in _modules():
        imported = _imports_from_ops_config_io(tree)
        defined = {
            n.name
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            value = node.value
            is_alias = (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id in defined
                and not value.args
                and not value.keywords
            )
            if not is_alias and _mentions_ops_config_io(value, imported):
                offenders.append(
                    f"{path.relative_to(PACKAGE)}:{node.lineno}: {ast.unparse(node)[:80]}"
                )
    assert offenders == []
