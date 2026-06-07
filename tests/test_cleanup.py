from pathlib import Path

import pytest

from src.cleanup import CleanResult, PathTarget, dir_size


def test_dir_size_empty_dir(tmp_path):
    assert dir_size(tmp_path) == 0


def test_dir_size_missing_path(tmp_path):
    assert dir_size(tmp_path / "does-not-exist") == 0


def test_dir_size_sums_nested_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    sub = tmp_path / "sub" / "deeper"
    sub.mkdir(parents=True)
    (sub / "b.bin").write_bytes(b"x" * 250)
    assert dir_size(tmp_path) == 350


def test_dir_size_does_not_follow_symlinks(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 1000)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "link").symlink_to(outside)
    assert dir_size(cache) == 0


def _make_cache(root: Path, name: str, n_bytes: int) -> Path:
    cache = root / name
    cache.mkdir(parents=True)
    (cache / "data.bin").write_bytes(b"x" * n_bytes)
    return cache


def test_path_target_measure_sums_all_paths(tmp_path):
    a = _make_cache(tmp_path, "a", 100)
    b = _make_cache(tmp_path, "b", 200)
    target = PathTarget(key="t", label="T", paths=(str(a), str(b)))
    assert target.measure() == 300


def test_path_target_measure_missing_path_is_zero(tmp_path):
    target = PathTarget(key="t", label="T", paths=(str(tmp_path / "nope"),))
    assert target.measure() == 0


def test_path_target_clean_removes_dirs_and_reports_freed(tmp_path):
    a = _make_cache(tmp_path, "a", 100)
    b = _make_cache(tmp_path, "b", 200)
    target = PathTarget(key="t", label="T", paths=(str(a), str(b)))
    result = target.clean()
    assert result == CleanResult(freed=300, failed=())
    assert not a.exists()
    assert not b.exists()


def test_path_target_clean_missing_path_is_noop(tmp_path):
    target = PathTarget(key="t", label="T", paths=(str(tmp_path / "nope"),))
    assert target.clean() == CleanResult(freed=0, failed=())


def test_path_target_clean_reports_failures_without_raising(tmp_path):
    cache = tmp_path / "cache"
    locked = cache / "locked"
    locked.mkdir(parents=True)
    (locked / "stuck.bin").write_bytes(b"x" * 100)
    locked.chmod(0o500)  # no write permission: contents cannot be deleted
    try:
        target = PathTarget(key="t", label="T", paths=(str(cache),))
        result = target.clean()
        assert any("stuck.bin" in f or "locked" in f for f in result.failed)
    finally:
        locked.chmod(0o700)  # restore so pytest can clean tmp_path
