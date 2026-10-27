import os
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest

from hermes_ops_kit.env.atomic_write import atomic_write


def test_temp_is_private_before_content_is_published(tmp_path, monkeypatch):
    real_replace = os.replace
    seen = []

    def replace(source, target):
        seen.append(stat.S_IMODE(os.stat(source).st_mode))
        assert open(source).read() == "synthetic"
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", replace)
    atomic_write(str(tmp_path / "secret"), "synthetic")
    assert seen == [0o600]


def test_fixed_temp_symlink_is_never_followed(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    target = tmp_path / "secret"
    (tmp_path / "secret.tmp").symlink_to(victim)
    atomic_write(str(target), "synthetic")
    assert victim.read_text() == "untouched"
    assert target.read_text() == "synthetic"


def test_parallel_writes_are_complete(tmp_path):
    target = str(tmp_path / "secret")
    values = [str(i) * 10000 for i in range(12)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda value: atomic_write(target, value), values))
    assert (tmp_path / "secret").read_text() in values
    assert list(tmp_path.iterdir()) == [tmp_path / "secret"]


def test_failed_publication_preserves_previous_and_cleans_temp(tmp_path, monkeypatch):
    target = tmp_path / "secret"
    target.write_text("previous")

    def fail(*args):
        raise OSError("synthetic failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        atomic_write(str(target), "replacement")
    assert target.read_text() == "previous"
    assert list(tmp_path.iterdir()) == [target]
