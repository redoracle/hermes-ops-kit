"""Shared outbound URL rules."""

from __future__ import annotations

import pytest

from hermes_ops_kit.security import url_policy


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://h/x",
        "data:text/plain,hi",
        "http:///x",
        "http://u:p@h/x",
        "//h/x",
        "",
    ],
)
def test_require_http_url_refuses_non_http_forms(url):
    with pytest.raises(ValueError):
        url_policy.require_http_url(url)


@pytest.mark.parametrize(
    "url", ["http://example.com/x", "https://example.com:8443/x?y=1"]
)
def test_require_http_url_accepts_http_forms(url):
    assert url_policy.require_http_url(url).hostname == "example.com"


@pytest.mark.parametrize(
    "host,internal",
    [
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("172.16.0.1", True),
        ("192.168.0.1", True),
        ("169.254.169.254", True),
        ("100.64.0.1", True),
        ("::1", True),
        ("::ffff:10.0.0.1", True),
        ("fe80::1", True),
        ("224.0.0.1", True),
        ("93.184.216.34", False),
        ("2606:4700:4700::1111", False),
    ],
)
def test_is_internal_host(host, internal):
    assert url_policy.is_internal_host(host) is internal


def test_a_name_that_resolves_nowhere_is_not_reported_internal(monkeypatch):
    def fail(*_a, **_k):
        raise OSError("no such host")

    monkeypatch.setattr(url_policy.socket, "getaddrinfo", fail)
    assert url_policy.is_internal_host("nope.invalid") is False


def test_a_name_with_any_internal_address_is_internal(monkeypatch):
    infos = [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.9", 0))]
    monkeypatch.setattr(url_policy.socket, "getaddrinfo", lambda *a, **k: infos)
    assert url_policy.is_internal_host("mixed.example") is True


def test_allow_origins_is_case_insensitive_and_ignores_empty_entries():
    url_policy.require_public_url(
        "http://LOCALHOST/x", allow_origins=(("localhost", 80),)
    )
    with pytest.raises(ValueError):
        url_policy.require_public_url("http://localhost/x", allow_origins=(None,))


def test_an_allowed_origin_pins_the_port_and_defaults_are_normalized():
    allowed = (("localhost", 8080),)
    url_policy.require_public_url("http://localhost:8080/x", allow_origins=allowed)
    for other in (
        "http://localhost:8081/x",
        "http://localhost/x",
        "https://localhost:8080/x",
    ):
        if other.startswith("https://localhost:8080"):
            continue  # same host and port: the scheme does not change the origin pair
        with pytest.raises(ValueError):
            url_policy.require_public_url(other, allow_origins=allowed)
    url_policy.require_public_url(
        "https://localhost/x", allow_origins=(("localhost", 443),)
    )
    url_policy.require_public_url(
        "http://localhost/x", allow_origins=(("localhost", 80),)
    )


def test_origin_of():
    assert url_policy.origin_of("https://gw.lan:8443/v1") == ("gw.lan", 8443)
    assert url_policy.origin_of("https://GW.lan/v1") == ("gw.lan", 443)
    assert url_policy.origin_of("http://gw.lan") == ("gw.lan", 80)
    for bad in ("", None, "file:///x", "ftp://h/x", "not a url", "http://h:99999999/x"):
        assert url_policy.origin_of(bad) is None


def test_assistant_discover_rejects_bad_urls():
    from hermes_ops_kit import hermes_assistant_manager as am

    for bad in ("file:///etc", "http://u:p@h", "ftp://h"):
        result = am._discover_assistant(bad)
        assert result["ok"] is False and "rejected" in result["error"]


def test_assistant_ping_rejects_a_bad_base_url_before_any_request(
    tmp_path, monkeypatch
):
    import urllib.request

    from hermes_ops_kit import hermes_assistant_manager as am
    from hermes_ops_kit import ops_config_io

    monkeypatch.setattr(ops_config_io, "HERMES_HOME", str(tmp_path))
    cfg = tmp_path / "assistants.yaml"
    cfg.write_text(
        "assistants:\n  a1:\n    endpoint:\n      base_url_env: A1_BASE\n      api_key_env: A1_KEY\n"
    )
    monkeypatch.setenv("A1_BASE", "file:///etc")
    monkeypatch.setenv("A1_KEY", "x")

    def boom(*_a, **_k):
        raise AssertionError("a request was made")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    result = am._ping_assistant(str(cfg), "a1")
    assert result["ok"] is False and "rejected" in result["error"]


@pytest.mark.parametrize(
    "host",
    [
        "64:ff9b::a9fe:a9fe",  # NAT64 -> 169.254.169.254
        "64:ff9b::7f00:1",  # NAT64 -> 127.0.0.1
        "::127.0.0.1",
        "::ffff:0:127.0.0.1",
        "64:ff9b:1::1",
    ],
)
def test_ipv6_forms_that_embed_an_ipv4_address_are_internal(host):
    assert url_policy.is_internal_host(host) is True


def _server(handler_cls):
    import http.server
    import threading

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_an_authenticated_request_never_follows_a_redirect():
    import http.server
    import urllib.error
    import urllib.request

    seen = {"auth": None}

    class Target(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            seen["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    target = _server(Target)

    class Redirector(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(307)  # 307 would replay the method, body and headers
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/x")
            self.end_headers()

        def log_message(self, *a):
            pass

    front = _server(Redirector)
    req = urllib.request.Request(
        f"http://127.0.0.1:{front.server_port}/chat",
        data=b"{}",
        headers={"Authorization": "Bearer SECRET"},
    )
    try:
        with pytest.raises(urllib.error.URLError, match="redirects are not followed"):
            url_policy.open_without_redirects(req, timeout=5)
    finally:
        front.shutdown()
        target.shutdown()
    assert seen["auth"] is None


def test_open_without_redirects_still_serves_a_plain_response():
    import http.server
    import urllib.request

    class Ok(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hi")

        def log_message(self, *a):
            pass

    srv = _server(Ok)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/")
        assert url_policy.open_without_redirects(req, timeout=5).read() == b"hi"
    finally:
        srv.shutdown()


def test_open_without_redirects_has_no_file_handler():
    import urllib.error
    import urllib.request

    with pytest.raises(urllib.error.URLError):
        url_policy.open_without_redirects(
            urllib.request.Request("file:///etc/hosts"), timeout=5
        )
