from pathlib import Path

import pytest

from src.cleanup import dir_size


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
