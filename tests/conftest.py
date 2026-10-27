"""Keep collection-time constants and subprocesses away from operational state."""

import os
import tempfile


def pytest_configure(config):
    config._ops_test_home = tempfile.TemporaryDirectory(prefix="ops-kit-tests-")
    config._ops_previous_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_HOME"] = config._ops_test_home.name


def pytest_unconfigure(config):
    if not hasattr(config, "_ops_test_home"):
        return
    previous = config._ops_previous_home
    if previous is None:
        os.environ.pop("HERMES_HOME", None)
    else:
        os.environ["HERMES_HOME"] = previous
    config._ops_test_home.cleanup()
