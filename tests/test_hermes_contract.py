"""Opt-in contract test against a real Hermes checkout and its interpreter."""

import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
def test_current_hermes_directory_hooks_and_image_contract(tmp_path):
    source = os.environ.get("HERMES_AGENT_SOURCE")
    python = os.environ.get("HERMES_RUNTIME_PYTHON")
    if not source or not python:
        pytest.skip(
            "set HERMES_AGENT_SOURCE and HERMES_RUNTIME_PYTHON for real-core acceptance"
        )
    repo = Path(__file__).resolve().parents[1]
    script = r"""
import importlib, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
sys.path.insert(0, sys.argv[1])
from hermes_cli.plugins import PluginManager
from hermes_cli.plugins_manifest import parse_manifest_file
root = Path(sys.argv[2])
manifest = parse_manifest_file(root / "plugin.yaml", root, "user", "")
manager = PluginManager()
manager._load_plugin(manifest)
loaded = manager._plugins["hermes-ops-kit"]
assert loaded.enabled, loaded.error
assert len(loaded.tools_registered) == 7, loaded.tools_registered
assert manager.has_hook("transform_tool_result")
assert "hermes_ops_kit" not in sys.modules
from hermes_cli import lifecycle
lifecycle.has_hook = manager.has_hook
lifecycle.invoke_hook = manager.invoke_hook
from model_tools import _apply_transform_tool_result_hook
ids = SimpleNamespace(hook_kwargs=lambda: {})
secret = "sk-" + "X" * 40
def transform(value):
    return _apply_transform_tool_result_hook("synthetic", {}, value, 0, ids)
assert secret not in transform(secret)
assert secret not in transform({"nested": secret})
later = lambda **kwargs: "later transformer"
manager._hooks["transform_tool_result"].append(later)
assert transform("ordinary output") == "later transformer"
manager._hooks["transform_tool_result"].remove(later)
first = lambda **kwargs: "earlier transformer"
manager._hooks["transform_tool_result"].insert(0, first)
assert transform(secret) == "earlier transformer", "core ordering contract changed"
manager._hooks["transform_tool_result"].remove(first)
prefix = loaded.module.__name__ + ".hermes_ops_kit"
module = importlib.import_module(prefix + ".image_routes.hermes_provider")
router = importlib.import_module(prefix + ".image_routes.router")
provider = module.OpsKitRouterProvider()
provider._resolve_route_name = lambda: "fast"
router._load_config = lambda: {"default_route":"fast", "routes":{"fast":{"provider":"gemini"}}}
assert provider.capabilities()["max_reference_images"] == 1
generate = Mock(return_value={"ok":False,"error":"synthetic"})
router.generate = generate
provider.generate("Review image", image_url="/tmp/synthetic.png")
assert generate.call_args.kwargs["image_path"] == "/tmp/synthetic.png"
generate.reset_mock()
provider.generate("Review", reference_image_urls=["a.png", "b.png"])
generate.assert_not_called()
print("real directory loader, transform semantics/order, native image forwarding: PASS")
"""
    result = subprocess.run(
        [python, "-I", "-B", "-c", script, str(Path(source).resolve()), str(repo)],
        cwd=tmp_path,
        env={**os.environ, "HERMES_HOME": str(tmp_path / "home")},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
