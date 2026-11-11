"""Guard the tokenless PyPI release path against accidental regressions."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def test_pypi_publishing_uses_dedicated_oidc_job() -> None:
    source = WORKFLOW_PATH.read_text()
    workflow = yaml.safe_load(source)
    jobs = workflow["jobs"]

    # The release job must continue creating versions and GitHub Releases.
    release = jobs["release"]
    release_steps = release["steps"]
    assert any("semantic-release" in step.get("run", "") for step in release_steps)
    assert any(
        step.get("uses", "").startswith("actions/upload-artifact@")
        for step in release_steps
    )
    assert release["outputs"]["pypi_ready"] == (
        "${{ steps.pypi_release.outputs.ready }}"
    )

    # Do not grant OIDC to the job that installs dependencies and builds code.
    assert "id-token" not in workflow.get("permissions", {})
    assert "id-token" not in release.get("permissions", {})

    publisher = jobs["pypi-publish"]
    assert publisher["needs"] == "release"
    assert publisher["if"] == "needs.release.outputs.pypi_ready == 'true'"
    assert publisher["environment"]["name"] == "release"
    assert publisher["permissions"] == {"contents": "read", "id-token": "write"}
    assert any(
        step.get("uses", "").startswith("actions/download-artifact@")
        for step in publisher["steps"]
    )
    assert any(
        step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
        for step in publisher["steps"]
    )
    assert all("run" not in step for step in publisher["steps"])

    # The workflow must never depend on long-lived registry credentials.
    for legacy_auth in ("PYPI_API_TOKEN", "TWINE_PASSWORD", "TWINE_USERNAME"):
        assert legacy_auth not in source
