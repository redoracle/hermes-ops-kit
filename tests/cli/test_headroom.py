"""Hermes Ops Kit — Headroom overlay CLI tests.

Black-box tests of `headroom_ops/manager.py` against an isolated
HERMES_HOME.  A dummy in-process HTTP server stands in for a healthy
proxy (/readyz + /stats), so no real Headroom daemon is ever spawned:
the spawn path is exercised with a stripped PATH (binary not found).

Invariants under test:
  * fallback_providers are never rewritten;
  * enable → disable round-trips the model/providers sections exactly;
  * collisions (fallback pointing at the proxy) abort reconciliation;
  * a dead proxy keeps the route direct (warning, exit 0).
"""

from __future__ import annotations

import http.server
import json
import socket
import threading
from pathlib import Path

import pytest

from tests.cli.cli_runner import PROJECT_DIR, run_cli

CONFIG_TEMPLATE = """\
model:
  provider: nvidia
  default: nvidia/nemotron-3-nano-30b-a3b
  base_url: ""
providers:
  nvidia:
    api_key_env: NVIDIA_API_KEY
    base_url: https://integrate.api.nvidia.com/v1
fallback_providers:
  - provider: github
    model: gpt-5-mini
  - provider: openai
    model: gpt-5.4-mini
auxiliary:
  vision:
    provider: nvidia
    model: nvidia/nemotron-nano-12b-v2-vl
    base_url: ""
compression:
  enabled: true
"""


class _ProxyStub(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path in ("/readyz", "/livez"):
            body = b"ok"
        elif self.path == "/stats":
            body = json.dumps({"total_tokens_saved": 1234}).encode()
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def proxy_stub():
    """Dummy healthy proxy on an ephemeral port."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _ProxyStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def hermes_home(tmp_path: Path) -> Path:
    home = tmp_path / "hermes"
    (home / "ops-kit").mkdir(parents=True)
    (home / "config.yaml").write_text(CONFIG_TEMPLATE)
    (home / ".env").write_text("NVIDIA_API_KEY=test-key\n")
    return home


def _write_headroom_yaml(home: Path, *, enabled: bool, port: int) -> None:
    (home / "ops-kit" / "headroom.yaml").write_text(
        "headroom:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        f"  port: {port}\n"
        f'  base_url: "http://127.0.0.1:{port}/v1"\n'
        f'  run_dir: "{home}/ops-kit/run"\n'
        f'  state_file: "{home}/ops-kit/headroom_prev_route.json"\n'
    )


def _headroom(home: Path, *args: str, path: str | None = None):
    env = {"HERMES_HOME": str(home)}
    if path is not None:
        env["PATH"] = path
    return run_cli(
        [str(PROJECT_DIR / "headroom_ops" / "manager.py"), *args],
        env=env,
    )


def _load_config(home: Path) -> dict:
    import yaml

    return yaml.safe_load((home / "config.yaml").read_text())


def _result(res) -> dict:
    return json.loads(res.stdout)["result"]


def test_status_direct(hermes_home: Path):
    _write_headroom_yaml(hermes_home, enabled=False, port=_free_port())
    res = _headroom(hermes_home, "status", "--json")
    assert res.returncode == 0
    payload = _result(res)
    assert payload["desired"] == "disabled"
    assert payload["proxied"] is False
    assert payload["collisions"] == []


def test_reconcile_disabled_is_noop(hermes_home: Path):
    _write_headroom_yaml(hermes_home, enabled=False, port=_free_port())
    before = (hermes_home / "config.yaml").read_bytes()
    res = _headroom(hermes_home, "reconcile", "--json")
    assert res.returncode == 0
    assert _result(res)["action"] == "already_direct"
    assert (hermes_home / "config.yaml").read_bytes() == before


def test_enable_disable_roundtrip(hermes_home: Path, proxy_stub: int):
    _write_headroom_yaml(hermes_home, enabled=True, port=proxy_stub)
    original = _load_config(hermes_home)

    res = _headroom(hermes_home, "reconcile", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _result(res)["action"] == "enabled"

    cfg = _load_config(hermes_home)
    assert cfg["model"]["provider"] == "headroom"
    assert cfg["model"]["default"] == original["model"]["default"]
    entry = cfg["providers"]["headroom"]
    assert entry["base_url"] == f"http://127.0.0.1:{proxy_stub}/v1"
    assert entry["key_env"] == "NVIDIA_API_KEY"
    # The graceful-degradation invariant: fallbacks untouched.
    assert cfg["fallback_providers"] == original["fallback_providers"]
    assert (hermes_home / "ops-kit" / "headroom_prev_route.json").exists()

    # Reconcile again: idempotent.
    res = _headroom(hermes_home, "reconcile", "--json")
    assert _result(res)["action"] == "already_enabled"

    _write_headroom_yaml(hermes_home, enabled=False, port=proxy_stub)
    res = _headroom(hermes_home, "reconcile", "--json")
    assert res.returncode == 0
    assert _result(res)["action"] == "restored_direct"

    restored = _load_config(hermes_home)
    assert restored["model"] == original["model"]
    assert "headroom" not in restored["providers"]
    assert restored["fallback_providers"] == original["fallback_providers"]
    assert not (hermes_home / "ops-kit" / "headroom_prev_route.json").exists()


def test_enable_with_dead_proxy_stays_direct(hermes_home: Path):
    # Stripped PATH: the headroom binary cannot be found, the port is
    # closed — the route must stay direct with a warning, exit 0.
    _write_headroom_yaml(hermes_home, enabled=True, port=_free_port())
    before = _load_config(hermes_home)
    res = _headroom(hermes_home, "reconcile", "--json", path="/usr/bin:/bin")
    assert res.returncode == 0, res.stdout + res.stderr
    payload = _result(res)
    assert payload["proxied_after"] is False
    assert payload["warnings"]
    assert _load_config(hermes_home) == before


def test_collision_guard_blocks_reconcile(hermes_home: Path, proxy_stub: int):
    config = (hermes_home / "config.yaml").read_text() + (
        "  # collision injected by test\n"
    )
    config = config.replace(
        "  - provider: openai\n    model: gpt-5.4-mini\n",
        "  - provider: openai\n    model: gpt-5.4-mini\n"
        f"    base_url: http://127.0.0.1:{proxy_stub}/v1\n",
    )
    (hermes_home / "config.yaml").write_text(config)
    _write_headroom_yaml(hermes_home, enabled=True, port=proxy_stub)

    res = _headroom(hermes_home, "reconcile", "--json")
    assert res.returncode == 1
    payload = json.loads(res.stdout)
    assert any("fallback" in e["message"] for e in payload["errors"])
    cfg = _load_config(hermes_home)
    assert cfg["model"]["provider"] == "nvidia"  # untouched


def test_doctor_flags_collision(hermes_home: Path, proxy_stub: int):
    _write_headroom_yaml(hermes_home, enabled=False, port=proxy_stub)
    config = (
        (hermes_home / "config.yaml")
        .read_text()
        .replace(
            "    base_url: https://integrate.api.nvidia.com/v1",
            f"    base_url: http://127.0.0.1:{proxy_stub}/v1",
        )
    )
    (hermes_home / "config.yaml").write_text(config)
    res = _headroom(hermes_home, "doctor", "--json")
    assert res.returncode == 1


def test_stats_via_stub(hermes_home: Path, proxy_stub: int):
    _write_headroom_yaml(hermes_home, enabled=False, port=proxy_stub)
    res = _headroom(hermes_home, "stats", "--json")
    assert res.returncode == 0
    assert _result(res)["total_tokens_saved"] == 1234


def _daemon_settings(home: Path, port: int) -> dict:
    """Explicit settings dict for in-process daemon tests.

    ``load_settings()`` resolves paths through module-level constants
    frozen at first import (ops_config_io.HERMES_HOME), which makes it
    test-order dependent inside a single pytest process.
    """
    return {
        "enabled": True,
        "port": port,
        "base_url": f"http://127.0.0.1:{port}/v1",
        "run_dir": str(home / "ops-kit" / "run"),
        "memory_project_root": str(home),
        "startup_timeout_seconds": 5,
        "proxy_flags": [],
    }


def test_upstream_drift_triggers_restart(hermes_home: Path, proxy_stub: int):
    """A healthy proxy forwarding to a stale upstream must be restarted."""
    import sys

    sys.path.insert(0, str(PROJECT_DIR))
    from hermes_ops_kit.headroom_ops import daemon

    settings = _daemon_settings(hermes_home, proxy_stub)
    run_dir = Path(settings["run_dir"])
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / f"headroom-{proxy_stub}.meta").write_text(
        json.dumps(
            {
                "port": proxy_stub,
                "upstream_url": "https://old.example.com/v1",
                "flags": [],
                "pid": 99999,
            }
        )
    )

    res = daemon.up(settings, "https://new.example.com/v1", dry_run=True)
    assert "upstream drift" in res["message"]

    # Same upstream (modulo trailing slash): no restart.
    res = daemon.up(settings, "https://old.example.com/v1/", dry_run=True)
    assert res["message"] == "proxy already healthy"


def test_provider_switch_reapplies_overlay(hermes_home: Path, proxy_stub: int):
    """`hermes model` switching provider orphans the overlay; the next
    reconcile must re-apply it over the NEW provider, and disable must
    not resurrect the stale managed entry."""
    _write_headroom_yaml(hermes_home, enabled=True, port=proxy_stub)
    res = _headroom(hermes_home, "reconcile", "--json")
    assert _result(res)["action"] == "enabled"

    # Simulate `hermes model`: the picker overwrites model.provider,
    # leaving the managed providers.headroom entry behind.
    import yaml

    cfg = _load_config(hermes_home)
    cfg["model"]["provider"] = "openai"
    cfg["model"]["default"] = "gpt-5.4-mini"
    cfg["providers"]["openai"] = {
        "api_key_env": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
    }
    (hermes_home / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False))

    res = _headroom(hermes_home, "reconcile", "--json")
    assert res.returncode == 0, res.stdout + res.stderr
    assert _result(res)["action"] == "enabled"

    cfg = _load_config(hermes_home)
    assert cfg["model"]["provider"] == "headroom"
    assert cfg["providers"]["headroom"]["key_env"] == "OPENAI_API_KEY"

    _write_headroom_yaml(hermes_home, enabled=False, port=proxy_stub)
    res = _headroom(hermes_home, "reconcile", "--json")
    assert _result(res)["action"] == "restored_direct"
    cfg = _load_config(hermes_home)
    assert cfg["model"]["provider"] == "openai"
    assert "headroom" not in cfg["providers"]


def test_up_force_restarts_hung_proxy(hermes_home: Path, tmp_path: Path):
    """A pid that is alive but not answering /readyz (hung proxy holding
    the port) must be terminated before the new spawn — otherwise the new
    process cannot bind and the pidfile overwrite leaks the hung one."""
    import os
    import subprocess
    import sys
    import time

    sys.path.insert(0, str(PROJECT_DIR))
    hung_pid: int | None = None
    try:
        from hermes_ops_kit.headroom_ops import daemon

        port = _free_port()
        settings = _daemon_settings(hermes_home, port)
        run_dir = Path(settings["run_dir"])
        run_dir.mkdir(parents=True, exist_ok=True)

        # Detached sleeper (double fork: no zombie masking _pid_alive).
        out = subprocess.run(
            ["/bin/sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"],
            capture_output=True,
            text=True,
            check=True,
        )
        hung_pid = int(out.stdout.strip())
        (run_dir / f"headroom-{port}.pid").write_text(str(hung_pid))

        bindir = tmp_path / "bin"
        bindir.mkdir()
        fake = bindir / "headroom"
        fake.write_text("#!/bin/sh\nexit 7\n")
        fake.chmod(0o755)
        old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bindir}:/usr/bin:/bin"
        try:
            res = daemon.up(settings, "https://x.example.com/v1")
        finally:
            os.environ["PATH"] = old_path

        # The hung process was terminated before the new spawn…
        deadline = time.time() + 10
        gone = False
        while time.time() < deadline and not gone:
            try:
                os.kill(hung_pid, 0)
                time.sleep(0.2)
            except ProcessLookupError:
                gone = True
        assert gone, "hung proxy was not terminated"
        # …and the replacement's early exit is reported faithfully.
        assert res["ok"] is False
        assert "exited early" in res["message"]
    finally:
        if hung_pid:
            try:
                os.kill(hung_pid, 9)  # safety net if the kill path regressed
            except ProcessLookupError:
                pass


def test_preflight_reconcile_is_best_effort():
    """enforce._reconcile_headroom always returns a dict (never raises)."""
    import sys

    sys.path.insert(0, str(PROJECT_DIR))
    from hermes_ops_kit.security.plugin_scanner.enforce import _reconcile_headroom

    res = _reconcile_headroom(dry_run=True)
    assert isinstance(res, dict)
    assert "action" in res
    assert "errors" in res
