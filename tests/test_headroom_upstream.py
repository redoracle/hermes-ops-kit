"""Which endpoint Headroom proxies: `providers:` or Hermes' `custom_providers:` list."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from hermes_ops_kit.headroom_ops import reconcile as rec

URL = "http://llm.example.test:8080/v1"


def _cfg(**kwargs):
    return {
        "model": {"provider": kwargs.pop("primary", "custom:whiplash-local")},
        **kwargs,
    }


def test_the_shape_of_a_real_install_resolves():
    entry = rec.resolve_provider_entry(
        _cfg(
            custom_providers=[
                {"name": "whiplash-local", "base_url": URL, "key_env": "K"}
            ]
        ),
        "custom:whiplash-local",
    )
    assert entry["base_url"] == URL and entry["key_env"] == "K"


@pytest.mark.parametrize(
    "display_name,provider_key,requested",
    [
        ("whiplash-local", "", "custom:whiplash-local"),
        ("whiplash-local", "", "whiplash-local"),
        ("whiplash-local", "", "CUSTOM:Whiplash-Local"),
        ("Whiplash Local", "", "custom:whiplash-local"),
        ("Whiplash Local", "", "whiplash local"),
        ("display", "my-key", "custom:my-key"),
        ("display", "custom:my-key", "my-key"),
        ("display", "custom:my-key", "custom:custom:my-key"),
    ],
)
def test_names_resolve_the_way_hermes_core_resolves_them(
    display_name, provider_key, requested
):
    cfg = {
        "custom_providers": [
            {"name": display_name, "provider_key": provider_key, "base_url": URL}
        ]
    }
    assert rec.resolve_provider_entry(cfg, requested)["base_url"] == URL


@pytest.mark.parametrize("key", ["base_url", "url", "api"])
def test_core_endpoint_key_aliases_are_accepted(key):
    cfg = {"custom_providers": [{"name": "x", key: URL}], "providers": {}}
    assert rec.resolve_provider_entry(cfg, "custom:x")["base_url"] == URL


def test_a_providers_block_with_an_endpoint_wins_over_the_list():
    cfg = {
        "providers": {
            "custom:x": {"base_url": "http://winner.example.test/v1", "key_env": "A"}
        },
        "custom_providers": [{"name": "x", "base_url": URL, "key_env": "B"}],
    }
    entry = rec.resolve_provider_entry(cfg, "custom:x")
    assert (
        entry["base_url"] == "http://winner.example.test/v1" and entry["key_env"] == "A"
    )


def test_a_providers_block_without_an_endpoint_is_tuning_not_a_definition():
    """Core treats it as settings for a built-in provider and keeps looking."""
    cfg = {
        "providers": {"custom:x": {"stale_timeout_seconds": 600}},
        "custom_providers": [{"name": "x", "base_url": URL}],
    }
    assert rec.resolve_provider_entry(cfg, "custom:x")["base_url"] == URL


@pytest.mark.parametrize(
    "cfg,requested",
    [
        ({}, "custom:x"),
        ({"custom_providers": []}, "custom:x"),
        ({"custom_providers": None}, "custom:x"),
        ({"custom_providers": "nonsense"}, "custom:x"),
        (
            {"custom_providers": ["not-a-dict", {"name": "x"}]},
            "custom:x",
        ),  # no endpoint
        ({"custom_providers": [{"name": "other", "base_url": URL}]}, "custom:x"),
        ({"custom_providers": [{"name": "x", "base_url": URL}]}, ""),
    ],
)
def test_nothing_resolves_for_unknown_or_malformed_input(cfg, requested):
    assert rec.resolve_provider_entry(cfg, requested) == {}


def test_upstream_provider_entry_returns_the_provider_and_its_entry():
    cfg = _cfg(
        custom_providers=[{"name": "whiplash-local", "base_url": URL, "key_env": "K"}]
    )
    provider, entry = rec.upstream_provider_entry(cfg, {"state_file": "/nonexistent"})
    assert provider == "custom:whiplash-local" and entry["base_url"] == URL


def test_when_proxied_the_snapshot_names_the_upstream(tmp_path):
    state = tmp_path / "prev.json"
    state.write_text(json.dumps({"model": {"provider": "custom:whiplash-local"}}))
    cfg = {
        "model": {"provider": "headroom"},
        "custom_providers": [
            {"name": "whiplash-local", "base_url": URL, "key_env": "K"}
        ],
    }
    provider, entry = rec.upstream_provider_entry(cfg, {"state_file": str(state)})
    assert provider == "custom:whiplash-local" and entry["base_url"] == URL


# ── Contract: the kit's name matching must equal Hermes core's ──────────────────────────

CASES = [
    ("whiplash-local", "", "custom:whiplash-local"),
    ("whiplash-local", "", "whiplash-local"),
    ("Whiplash Local", "", "custom:whiplash-local"),
    ("Whiplash Local", "", "WHIPLASH LOCAL"),
    ("display", "my-key", "custom:my-key"),
    ("display", "custom:my-key", "my-key"),
    ("whiplash-local", "", "custom:other"),
    ("whiplash-local", "", "openai"),
]

CORE_SCRIPT = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from hermes_cli.providers import resolve_custom_provider
out = []
for name, key, requested in json.loads(sys.argv[2]):
    entry = {"name": name, "base_url": "http://llm.example.test:8080/v1"}
    if key:
        entry["provider_key"] = key
    pdef = resolve_custom_provider(requested, [entry])
    out.append(pdef.base_url if pdef else None)
print(json.dumps(out))
"""


@pytest.mark.integration
def test_name_matching_agrees_with_hermes_core():
    source = os.environ.get("HERMES_AGENT_SOURCE")
    python = os.environ.get("HERMES_RUNTIME_PYTHON")
    if not source:
        pytest.skip("set HERMES_AGENT_SOURCE for the real-core contract test")
    if not (python and os.path.isfile(python) and os.access(python, os.X_OK)):
        python = str(Path(source).expanduser() / "venv" / "bin" / "python")
    if not os.access(python, os.X_OK):
        pytest.skip(f"no executable interpreter for Hermes core ({python})")
    done = subprocess.run(
        [
            python,
            "-I",
            "-c",
            CORE_SCRIPT,
            str(Path(source).expanduser()),
            json.dumps(CASES),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-800:]
    core = json.loads(done.stdout.strip().splitlines()[-1])
    kit = []
    for name, key, requested in CASES:
        entry = {"name": name, "base_url": URL}
        if key:
            entry["provider_key"] = key
        found = rec.resolve_provider_entry({"custom_providers": [entry]}, requested)
        kit.append(found.get("base_url"))
    assert kit == [("http://llm.example.test:8080/v1" if c else None) for c in core]
