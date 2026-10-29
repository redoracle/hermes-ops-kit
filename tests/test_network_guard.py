"""The suite must not be able to reach a real provider (fake keys once did)."""

import socket

import pytest


def test_a_non_loopback_connection_is_refused(_no_outbound_network):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(ConnectionRefusedError, match="192.0.2.1"):
            sock.connect(("192.0.2.1", 443))  # TEST-NET-1: never routable
    assert _no_outbound_network == [("192.0.2.1", 443)]
    _no_outbound_network.clear()  # provoked on purpose


def test_a_hostname_is_refused_before_it_resolves(_no_outbound_network):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(ConnectionRefusedError, match="api.openai.com"):
            sock.connect(("api.openai.com", 443))
        assert sock.connect_ex(("api.openai.com", 443)) != 0
    assert len(_no_outbound_network) == 2
    _no_outbound_network.clear()


def test_a_swallowed_attempt_still_fails_the_test(tmp_path):
    """Code that treats a refused connection as "offline" must not hide the attempt."""
    import subprocess
    import sys
    from pathlib import Path

    (tmp_path / "conftest.py").write_text(
        (Path(__file__).parent / "conftest.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "test_swallow.py").write_text(
        "import socket\n"
        "def test_swallowed():\n"
        "    try:\n"
        "        socket.create_connection(('192.0.2.1', 443), timeout=1)\n"
        "    except OSError:\n"
        "        pass\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            str(tmp_path),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert done.returncode != 0, done.stdout
    assert "test tried to reach the network" in done.stdout, done.stdout


def test_loopback_still_works():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        client = socket.create_connection(server.getsockname(), timeout=2)
        client.close()
    finally:
        server.close()


@pytest.mark.network
def test_the_network_marker_opts_out():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        try:
            sock.connect(("192.0.2.1", 9))  # unroutable: times out or is unreachable
        except OSError:
            pass  # reaching the OS (not the guard's refusal) is the point
