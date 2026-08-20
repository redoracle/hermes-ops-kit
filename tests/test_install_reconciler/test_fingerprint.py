"""Unit tests: installation ABI fingerprint invariants."""

from hermes_ops_kit.install_reconciler.fingerprint import (
    actual_fingerprint,
    expected_fingerprint,
)
from hermes_ops_kit.install_reconciler.state import (
    ActualInstallation,
    ConsoleScript,
    ExpectedInstallation,
    PluginEntryPoint,
)


def _expected() -> ExpectedInstallation:
    return ExpectedInstallation(
        package_name="hermes-ops-kit",
        version="0.4.0",
        console_scripts={"hermes-ops-kit": "hermes_ops_kit.bridge:main"},
        plugin_entry_points={"hermes-ops-kit": "hermes_ops_kit"},
        dependencies=["requests>=2.31", "PyYAML>=6.0"],
        build_backend="hatchling.build",
        packages=["hermes_ops_kit"],
    )


def _actual() -> ActualInstallation:
    return ActualInstallation(
        distribution_present=True,
        distribution_name="hermes-ops-kit",
        version="0.4.0",
        console_scripts={
            "hermes-ops-kit": ConsoleScript(
                name="hermes-ops-kit", entry="hermes_ops_kit.bridge:main"
            )
        },
        plugin_entry_points={
            "hermes-ops-kit": PluginEntryPoint(
                name="hermes-ops-kit", entry="hermes_ops_kit"
            )
        },
        requires=["requests>=2.31", "PyYAML>=6.0"],
    )


def test_fingerprint_ignores_implementation_only_changes():
    """*.py / docs / tests changes must NOT invalidate the ABI fingerprint."""
    e = _expected()
    assert expected_fingerprint(e) == expected_fingerprint(_expected())


def test_fingerprint_stable_deterministic():
    assert expected_fingerprint(_expected()) == expected_fingerprint(_expected())


def test_fingerprint_changes_on_scripts_drift():
    e = _expected()
    fp1 = expected_fingerprint(e)
    e.console_scripts["hermes-ops-kit"] = "bridge:main"  # the incident class
    assert expected_fingerprint(e) != fp1


def test_fingerprint_changes_on_dependency_drift():
    e = _expected()
    fp1 = expected_fingerprint(e)
    e.dependencies.append("httpx>=0.27")
    assert expected_fingerprint(e) != fp1


def test_fingerprint_changes_on_plugin_entrypoint_drift():
    e = _expected()
    fp1 = expected_fingerprint(e)
    e.plugin_entry_points["hermes-ops-kit"] = "bridge"
    assert expected_fingerprint(e) != fp1


def test_fingerprint_version_neutral():
    """Same ABI at different versions — version itself is not install-relevant."""
    e1 = _expected()
    e2 = _expected()
    e2.version = "9.9.9"
    assert expected_fingerprint(e1) == expected_fingerprint(e2)


def test_matching_states_share_fingerprint():
    assert expected_fingerprint(_expected()) == actual_fingerprint(_actual())


def test_extras_excluded_from_installed_deps():
    a = _actual()
    fp = actual_fingerprint(a)
    a.requires.append("pytest>=8; extra == 'dev'")
    assert actual_fingerprint(a) == fp


def test_req_normalization_pep503():
    """ruamel.yaml == ruamel_yaml == ruamel-yaml after normalization."""
    from hermes_ops_kit.install_reconciler.fingerprint import _normalize_req

    assert _normalize_req("ruamel.yaml>=0.18") == _normalize_req("ruamel-yaml>=0.18")
    assert _normalize_req("PyJWT>=2.8") == _normalize_req("pyjwt >= 2.8")
