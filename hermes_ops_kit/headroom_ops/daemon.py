"""Hermes Ops Kit — Headroom proxy lifecycle.

Self-contained daemon management (no shell aliases, no external
supervisors): idempotent ``up`` (health-check first), pidfile-based
``down``, and ``status``.  Pidfile, launch metadata, and logs live in
``~/.hermes/ops-kit/run``.

The no-coding profile is enforced at spawn time: ``--code-aware``,
``--code-graph`` and ``--learn`` are stripped, ``--no-code-aware`` is
always appended (see settings.sanitized_proxy_flags).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import urllib.request


from ..headroom_ops.settings import proxy_root, sanitized_proxy_flags  # noqa: E402
from ..security.redaction import sanitize_url_for_display  # noqa: E402

READYZ_PATH = "/readyz"
STATS_PATH = "/stats"


def _run_paths(settings: dict) -> tuple[str, str, str]:
    run_dir = settings["run_dir"]
    port = int(settings.get("port", 8790))
    pid_file = os.path.join(run_dir, f"headroom-{port}.pid")
    meta_file = os.path.join(run_dir, f"headroom-{port}.meta")
    log_file = os.path.join(run_dir, f"headroom-{port}.log")
    return pid_file, meta_file, log_file


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    """GET *url*; returns (status, body). (0, error) on connection failure."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", "replace")
    except Exception as exc:
        return 0, str(exc)


def is_healthy(settings: dict, timeout: float = 3.0) -> bool:
    """True when the proxy answers /readyz on the configured port."""
    status, _ = _http_get(proxy_root(settings) + READYZ_PATH, timeout=timeout)
    return status == 200


def get_stats(settings: dict) -> dict | None:
    """Return parsed /stats payload, or None when unreachable."""
    status, body = _http_get(proxy_root(settings) + STATS_PATH, timeout=5.0)
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def read_meta(settings: dict) -> dict:
    """Launch metadata recorded by the last ``up`` (empty when absent)."""
    _, meta_file, _ = _run_paths(settings)
    try:
        with open(meta_file) as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _read_pid(pid_file: str) -> int | None:
    try:
        with open(pid_file) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def status(settings: dict) -> dict:
    """Daemon status snapshot: pid, alive, healthy, meta."""
    pid_file, _, log_file = _run_paths(settings)
    pid = _read_pid(pid_file)
    alive = bool(pid and _pid_alive(pid))
    return {
        "pid": pid,
        "alive": alive,
        "healthy": is_healthy(settings),
        "log_file": log_file,
        "meta": read_meta(settings),
    }


def up(settings: dict, upstream_url: str, dry_run: bool = False) -> dict:
    """Start the proxy if not already healthy (idempotent).

    Returns {ok, started, healthy, message}.  *upstream_url* is the
    OpenAI-compatible endpoint Headroom forwards to (resolved by the
    caller from the Hermes provider config).
    """
    if is_healthy(settings):
        # Upstream drift (e.g. the operator switched the primary provider
        # via `hermes model`): a healthy proxy still forwarding to the old
        # endpoint must be restarted, or the proxied route silently breaks.
        running = str(read_meta(settings).get("upstream_url") or "")
        if (
            running
            and upstream_url
            and (running.rstrip("/") != upstream_url.rstrip("/"))
        ):
            if dry_run:
                return {
                    "ok": True,
                    "started": False,
                    "healthy": True,
                    "dry_run": True,
                    "message": "would restart: upstream drift "
                    f"({running} → {upstream_url})",
                }
            down(settings)
        else:
            return {
                "ok": True,
                "started": False,
                "healthy": True,
                "message": "proxy already healthy",
            }

    binary = shutil.which("headroom")
    if not binary:
        return {
            "ok": False,
            "started": False,
            "healthy": False,
            "message": "headroom binary not found on PATH "
            "(install: pipx install headroom-ai)",
        }
    if not upstream_url:
        return {
            "ok": False,
            "started": False,
            "healthy": False,
            "message": "no OpenAI-compatible upstream URL resolved",
        }

    port = int(settings.get("port", 8790))
    cmd = [
        binary,
        "proxy",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--openai-api-url",
        upstream_url,
        "--memory-project-root",
        settings["memory_project_root"],
        *sanitized_proxy_flags(settings),
    ]
    if dry_run:
        # Build a display-safe command line with the upstream URL sanitized.
        display_cmd = list(cmd)
        for i, arg in enumerate(display_cmd):
            if arg == "--openai-api-url" and i + 1 < len(display_cmd):
                display_cmd[i + 1] = sanitize_url_for_display(display_cmd[i + 1])
        return {
            "ok": True,
            "started": False,
            "healthy": False,
            "dry_run": True,
            "message": "would start: " + " ".join(display_cmd),
        }

    pid_file, meta_file, log_file = _run_paths(settings)
    os.makedirs(settings["run_dir"], exist_ok=True)

    # A stale pidfile from a crashed proxy must not mask reality.
    stale_pid = _read_pid(pid_file)
    if stale_pid and not _pid_alive(stale_pid):
        for path in (pid_file, meta_file):
            try:
                os.remove(path)
            except OSError:
                pass
    elif stale_pid:
        # Alive but unhealthy (hung proxy still holding the port): free it
        # before spawning, or the new process fails to bind and the pidfile
        # overwrite would leak the hung one.
        down(settings)

    with open(log_file, "ab") as log:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    with open(pid_file, "w") as f:
        f.write(str(proc.pid))
    with open(meta_file, "w") as f:
        json.dump(
            {
                "port": port,
                "upstream_url": sanitize_url_for_display(upstream_url),
                "flags": sanitized_proxy_flags(settings),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "pid": proc.pid,
            },
            f,
            indent=2,
        )

    deadline = time.time() + float(settings.get("startup_timeout_seconds", 20))
    while time.time() < deadline:
        if is_healthy(settings, timeout=1.5):
            return {
                "ok": True,
                "started": True,
                "healthy": True,
                "message": f"proxy started (pid {proc.pid})",
            }
        if proc.poll() is not None:
            return {
                "ok": False,
                "started": True,
                "healthy": False,
                "message": f"proxy exited early (rc={proc.returncode}); see {log_file}",
            }
        time.sleep(0.5)
    return {
        "ok": False,
        "started": True,
        "healthy": False,
        "message": f"proxy did not become healthy within timeout; see {log_file}",
    }


def down(settings: dict, grace_seconds: float = 8.0) -> dict:
    """Stop the proxy via pidfile (TERM, then KILL). Idempotent."""
    pid_file, meta_file, _ = _run_paths(settings)
    pid = _read_pid(pid_file)
    if not pid or not _pid_alive(pid):
        for path in (pid_file, meta_file):
            try:
                os.remove(path)
            except OSError:
                pass
        return {"ok": True, "stopped": False, "message": "proxy not running"}

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return {"ok": False, "stopped": False, "message": f"SIGTERM failed: {exc}"}
    deadline = time.time() + grace_seconds
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.3)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    for path in (pid_file, meta_file):
        try:
            os.remove(path)
        except OSError:
            pass
    return {"ok": True, "stopped": True, "message": f"proxy stopped (pid {pid})"}
