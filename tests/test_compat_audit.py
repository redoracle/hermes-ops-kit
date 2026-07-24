"""Tests for scripts/hermes_compat_audit.py — fetch edge cases + summary shape."""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import hermes_compat_audit as ca  # pyright: ignore[reportMissingImports]


class _Resp:
    def __init__(self, data: object) -> None:
        self._data = data

    def read(self) -> bytes:
        return json.dumps(self._data).encode()

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: object) -> bool:
        return False


def test_empty_releases_list_handled(monkeypatch) -> None:
    monkeypatch.setattr(ca.urllib.request, "urlopen", lambda req, timeout=20: _Resp([]))
    d, err = ca._fetch_json(ca.RELEASES_API)
    assert err is None
    assert d == []


def test_non_list_response_handled(monkeypatch) -> None:
    monkeypatch.setattr(
        ca.urllib.request, "urlopen", lambda req, timeout=20: _Resp({"not": "a list"})
    )
    d, err = ca._fetch_json(ca.RELEASES_API)
    assert err is None
    assert isinstance(d, dict)


def test_http_error_handled(monkeypatch) -> None:
    def _raise(req, timeout=20):
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {}, io.BytesIO(b"rate limited")
        )

    monkeypatch.setattr(ca.urllib.request, "urlopen", _raise)
    d, err = ca._fetch_json(ca.RELEASES_API)
    assert d is None
    assert err is not None
    assert "403" in err


def test_summarize_release_shape() -> None:
    rel = {
        "tag_name": "v2026.7.20",
        "name": "Hermes Agent v0.19.0 (v2026.7.20)",
        "published_at": "2026-07-20T18:35:55Z",
        "prerelease": False,
        "html_url": "https://example/release",
        "body": "Quicksilver",
    }
    s = ca._summarize_release(rel)
    assert s["tag"] == "v2026.7.20"
    assert s["body_preview"] == "Quicksilver"
    assert s["prerelease"] is False
