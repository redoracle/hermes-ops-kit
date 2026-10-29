"""Provider-returned image URLs are untrusted: http(s) only, public hosts, capped, atomic."""

from __future__ import annotations

import http.server
import threading

import pytest

from hermes_ops_kit.image_routes.adapters import (
    cloudflare_image,
    custom_image,
    fal_image,
    openai_image,
)
from hermes_ops_kit.image_routes.adapters.base import _http_opener, download_image
from hermes_ops_kit.security.url_policy import origin_of

ADAPTERS = [
    openai_image.OpenAIImageAdapter,
    custom_image.CustomImageAdapter,
    fal_image.FALImageAdapter,
    cloudflare_image.CloudflareImageAdapter,
]


def _origin(server):
    """The test server is loopback: allow its origin explicitly, as an adapter would."""
    return (origin_of(server),)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/redirect-internal":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()
            return
        if self.path == "/redirect-ftp":
            self.send_response(302)
            self.send_header("Location", "ftp://example.invalid/x.png")
            self.end_headers()
            return
        body = b"x" * 2048
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_every_adapter_refuses_a_file_url(adapter, tmp_path):
    secret = tmp_path / "secret.env"
    secret.write_text("TOKEN=hunter2")
    target = tmp_path / "out.png"
    with pytest.raises(ValueError):
        adapter._download_image(secret.as_uri(), str(target))
    assert not target.exists()
    assert not (tmp_path / "out.png.download").exists()


@pytest.mark.parametrize(
    "url",
    [
        "data:image/png;base64,AAAA",
        "ftp://h/x.png",
        "http:///x",
        "httpfile://h/x",
    ],
)
def test_other_schemes_are_refused(url, tmp_path):
    with pytest.raises((ValueError, OSError)):
        download_image(url, str(tmp_path / "o.png"))
    assert list(tmp_path.iterdir()) == []


def test_http_download_is_written_atomically(server, tmp_path):
    target = tmp_path / "o.png"
    download_image(server + "/img.png", str(target), allow_origins=_origin(server))
    assert target.read_bytes() == b"x" * 2048
    assert not (tmp_path / "o.png.download").exists()


def test_size_cap_removes_the_partial_file(server, tmp_path):
    target = tmp_path / "o.png"
    with pytest.raises(ValueError, match="cap"):
        download_image(
            server + "/img.png",
            str(target),
            max_bytes=1024,
            allow_origins=_origin(server),
        )
    assert list(tmp_path.iterdir()) == []


def test_redirect_to_ftp_is_refused(server, tmp_path):
    with pytest.raises(OSError):
        download_image(
            server + "/redirect-ftp",
            str(tmp_path / "o.png"),
            allow_origins=_origin(server),
        )
    assert list(tmp_path.iterdir()) == []


def test_assistant_discover_refuses_non_http_urls():
    from hermes_ops_kit.hermes_assistant_manager import _discover_assistant

    result = _discover_assistant("file:///etc")
    assert result["ok"] is False and "http" in result["error"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:9/x.png",
        "http://localhost/x.png",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.5/x.png",
        "http://192.168.1.10/x.png",
        "http://100.64.0.1/x.png",
        "http://0.0.0.0/x.png",
        "http://[::1]/x.png",
        "http://[::ffff:127.0.0.1]/x.png",
        "http://user:pw@example.com/x.png",
    ],
)
def test_internal_and_credentialed_urls_are_refused_by_default(url, tmp_path):
    with pytest.raises(ValueError):
        download_image(url, str(tmp_path / "o.png"))
    assert list(tmp_path.iterdir()) == []


def test_a_configured_origin_is_allowed(server, tmp_path):
    download_image(
        server + "/img.png", str(tmp_path / "o.png"), allow_origins=_origin(server)
    )
    assert (tmp_path / "o.png").exists()


def test_the_allowance_is_for_that_origin_only(server, tmp_path):
    with pytest.raises(ValueError):
        download_image(
            server + "/img.png",
            str(tmp_path / "o.png"),
            allow_origins=(("example.com", 80),),
        )


def test_the_allowance_does_not_cover_another_port_of_the_same_host(server, tmp_path):
    """A response must not steer the fetch to a different service on the allowed host."""
    host, port = origin_of(server)
    with pytest.raises(ValueError):
        download_image(
            f"http://{host}:{port + 1}/img.png",
            str(tmp_path / "o.png"),
            allow_origins=((host, port),),
        )
    assert list(tmp_path.iterdir()) == []


def test_a_redirect_to_an_internal_address_is_refused(server, tmp_path):
    with pytest.raises(OSError, match="redirect refused"):
        download_image(
            server + "/redirect-internal",
            str(tmp_path / "o.png"),
            allow_origins=_origin(server),
        )
    assert list(tmp_path.iterdir()) == []


def test_adapters_pass_their_configured_origin(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(custom_image, "download_image", lambda *a, **k: seen.update(k))
    custom_image.CustomImageAdapter._download_image(
        "http://gw.lan/x.png",
        str(tmp_path / "o.png"),
        allow_origins=(("gw.lan", 80),),
    )
    assert seen["allow_origins"] == (("gw.lan", 80),)


def test_the_image_opener_raises_for_a_non_http_url_instead_of_returning_none():
    import urllib.error
    import urllib.request

    for url in ("file:///etc/hosts", "ftp://h/x", "data:text/plain,hi"):
        with pytest.raises(urllib.error.URLError):
            _http_opener().open(urllib.request.Request(url), timeout=5)
