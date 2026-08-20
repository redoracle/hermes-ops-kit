"""Deterministic route runtime harness tests.

These tests verify the harness itself and the existing route builders using
controlled config fixtures. No live provider calls are made.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import hermes_ops_kit.route_runtime_harness as rrh  # pyright: ignore[reportMissingImports]


HERMES_CFG = {
    "model": {"provider": "copilot", "default": "gpt-5.4-mini"},
    "auxiliary": {
        "vision": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "web_extract": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "compression": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "approval": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "skills_hub": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "mcp": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "title_generation": {"provider": "gemini", "model": "gemini-2.5-flash"},
        "triage_specifier": {"provider": "gemini", "model": "gemini-2.5-flash"},
    },
    "fallback_providers": [
        {"provider": "gemini", "model": "gemini-2.5-flash"},
        {"provider": "openai", "model": "gpt-5.4-mini"},
        {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
    ],
}

ROUTES_CFG = {
    "routes": {
        "utility": {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "label": "cheap utility · 1M ctx",
            "cost_class": "free-tier",
        },
        "aux": {
            "vision": {"label": "image/screenshot analysis"},
            "web": {"label": "web extraction"},
            "compression": {"label": "context compression"},
            "approval": {"label": "command risk scoring"},
            "skills": {"label": "skill discovery"},
            "mcp": {"label": "MCP helper"},
            "title": {"label": "session naming"},
            "triage": {"label": "task/spec expansion"},
        },
    }
}

IMAGE_CFG = {
    "default_route": "fast",
    "policies": {"prefer_local": False},
    "routes": {
        "local": {"provider": "local-comfyui", "model": "flux-local", "label": "local"},
        "fast": {
            "provider": "gemini",
            "model": "gemini-2.5-flash-image",
            "label": "fast",
        },
        "quality": {"provider": "openai", "model": "gpt-image-2", "label": "quality"},
        "fallback": {"provider": "fal", "model": "flux-pro", "label": "fallback"},
    },
}

MCP_CFG = {
    "mcp_servers": {
        "obsidian-mcp-vault": {"url": "http://127.0.0.1:3333", "enabled": True},
        "git-mcp": {"command": "git-mcp-server", "enabled": True},
    }
}

ASSISTANTS_CFG = {
    "assistants": {
        "<assistant-id>": {
            "enabled": True,
            "display_name": "Assistant Profiler ☁️",
            "type": "remote_hermes",
            "role": "security_profiler",
            "transport": "openai_chat_completions",
            "endpoint": {
                "base_url_env": "ASSISTANT_API_BASE",
                "api_key_env": "ASSISTANT_API_KEY",
                "model_env": "ASSISTANT_MODEL",
                "default_model": "hermes-agent",
            },
            "capabilities": [
                {"id": "review", "description": "Review", "safe_by_default": True}
            ],
        }
    }
}


def _fixture_results() -> dict:
    return rrh._all_online_results()


def test_harness_builds_expected_summary():
    report = rrh.build_report(
        HERMES_CFG, ROUTES_CFG, ASSISTANTS_CFG, IMAGE_CFG, MCP_CFG
    )
    assert report["summary"]["failed"] == 0
    assert report["summary"]["passed"] >= 6
    assert report["summary"]["total_routes_tested"] >= 10
    assert report["ok"] is True


def test_harness_primary_utility_and_aux_paths_are_explicit():
    report = rrh.build_report(
        HERMES_CFG, ROUTES_CFG, ASSISTANTS_CFG, IMAGE_CFG, MCP_CFG
    )
    routes = {entry["route"]: entry for entry in report["routes"]}

    assert routes["primary"]["actual_provider"] == "github"
    assert routes["primary"]["actual_model"] == "gpt-5.4-mini"
    assert routes["primary"]["runtime_path"] == "configured_path"

    assert routes["utility"]["actual_provider"] == "gemini"
    assert routes["utility"]["actual_model"] == "gemini-2.5-flash"

    for name in [
        "vision",
        "web",
        "compression",
        "approval",
        "skills",
        "mcp",
        "title",
        "triage",
    ]:
        entry = routes[name]
        assert entry["result"] == "passed"
        assert entry["actual_provider"] == "gemini"
        assert entry["actual_model"] == "gemini-2.5-flash"
        assert entry["runtime_path"] == "auxiliary"


def test_harness_detects_auto_aux_deviation():
    broken = json.loads(json.dumps(HERMES_CFG))
    broken["auxiliary"]["vision"] = {"provider": "auto", "model": ""}
    report = rrh.build_report(broken, ROUTES_CFG, ASSISTANTS_CFG, IMAGE_CFG, MCP_CFG)
    vision = next(r for r in report["routes"] if r["route"] == "vision")
    assert vision["result"] == "failed"
    assert vision["runtime_path"] == "auto_to_utility"
    assert "utility" in vision["failure_reason"].lower()


def test_harness_discovers_zai_primary_without_mislabeling_utility():
    """Z.AI must be discovered as primary, not confused with utility."""
    cfg = json.loads(json.dumps(HERMES_CFG))
    cfg["model"] = {"provider": "zai", "default": "glm-5.2"}
    report = rrh.build_report(cfg, ROUTES_CFG, ASSISTANTS_CFG, IMAGE_CFG, MCP_CFG)
    primary = next(r for r in report["routes"] if r["route"] == "primary")

    assert primary["result"] == "passed"
    assert primary["runtime_path"] == "configured_path"
    assert primary["recommended_fix"] == ""
    assert primary["actual_provider"] == "zai"
    assert primary["actual_model"] == "glm-5.2"


def test_harness_image_routes_are_distinct_from_aux_vision():
    report = rrh.build_report(
        HERMES_CFG, ROUTES_CFG, ASSISTANTS_CFG, IMAGE_CFG, MCP_CFG
    )
    image_fast = next(r for r in report["routes"] if r["route"] == "image.fast")
    assert image_fast["actual_provider"] == "gemini"
    assert image_fast["actual_model"] == "gemini-2.5-flash-image"
    assert image_fast["category"] == "IMAGE"


def test_harness_assistant_and_mcp_sections_exist():
    """Verify assistant and MCP entries appear in the harness report.

    Uses in-memory configs (ASSISTANTS_CFG, MCP_CFG) directly —
    no HermesFixture needed because build_report is config-driven.
    """
    report = rrh.build_report(
        HERMES_CFG, ROUTES_CFG, ASSISTANTS_CFG, IMAGE_CFG, MCP_CFG
    )
    assistant_entry = next(r for r in report["routes"] if r["category"] == "ASSISTANT")
    assert assistant_entry["route"] == "<assistant-id>"
    assert assistant_entry["runtime_path"] == "assistant_registry"
    assert any(r["category"] == "MCP" for r in report["routes"])


def test_cli_json_output(tmp_path: Path):
    hermes_cfg = tmp_path / "config.yaml"
    routes_cfg = tmp_path / "routes.yaml"
    assistants_cfg = tmp_path / "assistants.yaml"
    image_cfg = tmp_path / "image_routes.yaml"
    mcp_cfg = tmp_path / "mcp.yaml"
    hermes_cfg.write_text(json.dumps(HERMES_CFG))
    routes_cfg.write_text(json.dumps(ROUTES_CFG))
    assistants_cfg.write_text(json.dumps(ASSISTANTS_CFG))
    image_cfg.write_text(json.dumps(IMAGE_CFG))
    mcp_cfg.write_text(json.dumps(MCP_CFG))

    from subprocess import run
    import sys

    result = run(
        [
            sys.executable,
            "-P",
            "-m",
            "hermes_ops_kit.route_runtime_harness",
            "--hermes-config",
            str(hermes_cfg),
            "--routes-config",
            str(routes_cfg),
            "--assistants-config",
            str(assistants_cfg),
            "--image-config",
            str(image_cfg),
            "--mcp-config",
            str(mcp_cfg),
            "--json",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)},
    )

    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["ok"] is True
    assert data["summary"]["total_routes_tested"] >= 10
