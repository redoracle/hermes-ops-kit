"""Discover the actual installation state in the TARGET runtime.

Everything is observed via a single isolated probe executed with::

    <target-python> -I -c <probe>

``-I`` (isolated mode) keeps PYTHONPATH, user site and cwd out of the
probe, so the results reflect the runtime as pip/Hermes would see it.
The probe emits one JSON document on stdout; facts only, no judgment.

Primary sources: ``importlib.metadata`` and ``sysconfig``. The textual
content of generated wrappers (shebang) is collected as supplementary
evidence only — never as the authority.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .fingerprint import actual_fingerprint
from .state import (
    ActualInstallation,
    ConsoleScript,
    InstallationMode,
    PluginEntryPoint,
    RuntimeContext,
)

PROBE_TIMEOUT = 30

# Executed inside the target interpreter. importlib.metadata is the
# primary metadata source; EntryPoint.load() is the primary "does the
# runtime actually work" authority.
_PROBE = r"""
import json, shutil, sys, sysconfig
from pathlib import Path
from importlib.metadata import distributions, entry_points

NAME = "hermes_ops_kit"  # import name (dist name normalizes to this)
DIST = "__DIST_NAME__"

out = {
    "executable": sys.executable,
    "version": sys.version.split()[0],
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "scripts_dir": sysconfig.get_path("scripts"),
}

dists = [d for d in distributions()
         if (d.metadata["Name"] or "").lower().replace("_", "-") == DIST]
out["dist_count"] = len(dists)
if not dists:
    print(json.dumps(out))
    raise SystemExit(0)

d = dists[0]
out["dist_name"] = d.metadata["Name"]
out["dist_version"] = d.version
out["dist_info"] = str(getattr(d, "_path", ""))
out["requires"] = [str(r) for r in d.requires or []]

du = d.read_text("direct_url.json")
out["direct_url"] = du
if du:
    try:
        j = json.loads(du)
        out["origin_url"] = j.get("url", "")
        out["editable"] = bool(j.get("dir_info", {}).get("editable"))
    except ValueError:
        pass

eps = {}
for e in d.entry_points:
    eps.setdefault(e.group, []).append([e.name, e.value])
out["entry_points"] = eps

# Runtime probe: actually load each entry-point (the real authority).
loads = {}
for group, entries in eps.items():
    if group not in ("console_scripts", "hermes_agent.plugins"):
        continue
    for name, _value in entries:
        key = f"{group}:{name}"
        try:
            matches = [e for e in entry_points(group=group, name=name)
                       if (e.dist.metadata["Name"] or "").lower().replace("_", "-") == DIST]
            ep = matches[0]
            ep.load()
            loads[key] = {"ok": True, "error": ""}
        except Exception as exc:  # noqa: BLE001 — diagnostic probe
            loads[key] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
out["loads"] = loads

# Generated executables: script dir + PATH candidates (supplementary).
scripts = {}
for name in (n for n, _ in eps.get("console_scripts", [])):
    candidates = []
    sdir = out["scripts_dir"]
    p = shutil.which(name)
    sp = None
    if sdir:
        cand = Path(sdir) / name
        sp = str(cand) if cand.exists() else None
    scripts[name] = {"which": p, "script_dir": sp}
out["scripts"] = scripts
print(json.dumps(out))
"""


def _read_shebang(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            first = fh.readline(512)
        return first.decode("utf-8", "replace").strip()
    except OSError:
        return ""


def discover_actual_state(
    context: RuntimeContext, package_name: str = "hermes-ops-kit"
) -> ActualInstallation:
    """Run the isolated probe in the target runtime and map facts."""
    actual = ActualInstallation(distribution_name=package_name)
    dist_name = package_name.replace("_", "-")
    try:
        proc = subprocess.run(
            [
                context.python_executable,
                "-I",
                "-c",
                _PROBE.replace("__DIST_NAME__", dist_name),
            ],
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        actual.probe_error = f"{type(exc).__name__}: {exc}"
        return actual

    if proc.returncode != 0:
        actual.probe_error = f"probe rc={proc.returncode}: {proc.stderr.strip()[:500]}"
        return actual

    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        actual.probe_error = "probe emitted no parsable JSON"
        return actual

    # Environment facts back into the context (observed, not assumed).
    if not context.python_version:
        context.python_version = data.get("version", "")
    if not context.environment_prefix:
        context.environment_prefix = data.get("prefix", "")
    if not context.base_prefix:
        context.base_prefix = data.get("base_prefix", "")

    if data.get("dist_count", 0) == 0:
        return actual

    actual.distribution_present = True
    actual.version = data.get("dist_version", "")
    actual.dist_info_path = data.get("dist_info", "")
    actual.direct_url = data.get("direct_url") or ""
    actual.is_editable = bool(data.get("editable"))
    actual.origin_url = data.get("origin_url", "")
    actual.requires = data.get("requires", [])
    if data.get("dist_count", 0) > 1:
        actual.extra_distributions = ["<multiple dist-info matched>"]

    loads = data.get("loads", {})
    for name, value in data.get("entry_points", {}).get("console_scripts") or []:
        script = ConsoleScript(name=name, entry=value)
        rec = loads.get(f"console_scripts:{name}")
        if rec is not None:
            script.load_ok = rec["ok"]
            script.load_error = rec["error"]
        meta = (data.get("scripts") or {}).get(name) or {}
        script_path = meta.get("which") or meta.get("script_dir")
        if script_path:
            script.script_path = script_path
            script.shebang = _read_shebang(script_path)
        actual.console_scripts[name] = script

    for name, value in data.get("entry_points", {}).get("hermes_agent.plugins") or []:
        plugin = PluginEntryPoint(name=name, entry=value)
        rec = loads.get(f"hermes_agent.plugins:{name}")
        if rec is not None:
            plugin.load_ok = rec["ok"]
            plugin.load_error = rec["error"]
        actual.plugin_entry_points[name] = plugin

    if actual.is_editable:
        context.installation_mode = InstallationMode.EDITABLE
    else:
        context.installation_mode = InstallationMode.REGULAR

    actual.fingerprint = actual_fingerprint(actual)
    return actual


def resolve_runtime_context(
    target_python: str | None = None,
    plugin_source: str | None = None,
) -> RuntimeContext:
    """Build the context for an explicit target interpreter.

    Never silently assumes ``sys.executable``: when *target_python* is
    omitted the current interpreter is used, but that choice is recorded
    explicitly in the context.
    """
    import sys

    # NB: os.path.abspath, NOT Path.resolve() — a venv's bin/python is a
    # symlink to the base interpreter; resolving it would probe the BASE
    # python (no venv site-packages) and report MISSING_DISTRIBUTION on a
    # perfectly healthy venv install.
    exe = os.path.abspath(os.path.expanduser(str(target_python or sys.executable)))
    source = str(Path(plugin_source).resolve()) if plugin_source else ""
    return RuntimeContext(
        python_executable=exe,
        plugin_source=source,
        runtime_role="dev",
    )
