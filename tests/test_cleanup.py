import subprocess
from pathlib import Path

import pytest

from src import cleanup
from src.cleanup import CleanResult, DockerTarget, PathTarget, _parse_docker_size, dir_size


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


# Note: this test is a no-op when run as root (chmod has no effect for root).
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


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0B", 0),
        ("616.9kB (11%)", 616900),
        ("367.9MB (53%)", 367900000),
        ("6.663GB (96%)", 6663000000),
        ("2.923GB", 2923000000),
        ("1.5TB", 1500000000000),
    ],
)
def test_parse_docker_size(text, expected):
    assert _parse_docker_size(text) == expected


def test_parse_docker_size_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_docker_size("lots")


# Real output captured from `docker system df --format json` (2026-06-07).
DOCKER_DF_JSON = """\
{"Active":"14","Reclaimable":"6.663GB (96%)","Size":"6.936GB","TotalCount":"24","Type":"Images"}
{"Active":"13","Reclaimable":"616.9kB (11%)","Size":"5.54MB","TotalCount":"14","Type":"Containers"}
{"Active":"3","Reclaimable":"367.9MB (53%)","Size":"682.1MB","TotalCount":"8","Type":"Local Volumes"}
{"Active":"0","Reclaimable":"2.923GB","Size":"3.116GB","TotalCount":"201","Type":"Build Cache"}
"""


@pytest.fixture
def fake_docker_binary(tmp_path, monkeypatch):
    binary = tmp_path / "docker"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(cleanup, "_DOCKER_CANDIDATES", (str(binary),))
    return binary


def _fake_run(returncode=0, stdout="", stderr=""):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)

    return run


def test_docker_measure_sums_reclaimable_excluding_volumes(
    fake_docker_binary, monkeypatch
):
    monkeypatch.setattr(
        cleanup.subprocess, "run", _fake_run(stdout=DOCKER_DF_JSON)
    )
    # 6.663GB + 616.9kB + 2.923GB; the 367.9MB of volumes must NOT count.
    assert DockerTarget().measure() == 9586616900


def test_docker_measure_none_when_binary_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cleanup, "_DOCKER_CANDIDATES", (str(tmp_path / "no-docker"),)
    )
    assert DockerTarget().measure() is None


def test_docker_measure_none_when_daemon_down(fake_docker_binary, monkeypatch):
    monkeypatch.setattr(
        cleanup.subprocess,
        "run",
        _fake_run(returncode=1, stderr="Cannot connect to the Docker daemon"),
    )
    assert DockerTarget().measure() is None


def test_docker_measure_none_on_unrecognized_output(fake_docker_binary, monkeypatch):
    monkeypatch.setattr(
        cleanup.subprocess, "run", _fake_run(stdout="WARNING: upgrade your docker\n")
    )
    assert DockerTarget().measure() is None


def test_docker_clean_returns_freed_on_success(fake_docker_binary, monkeypatch):
    monkeypatch.setattr(
        cleanup.subprocess, "run", _fake_run(stdout=DOCKER_DF_JSON)
    )
    result = DockerTarget().clean()
    assert result.freed == 9586616900
    assert result.failed == ()


def test_docker_clean_reports_prune_failure(fake_docker_binary, monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if "df" in cmd:
            return subprocess.CompletedProcess(cmd, 0, DOCKER_DF_JSON, "")
        return subprocess.CompletedProcess(cmd, 1, "", "boom: prune exploded")

    monkeypatch.setattr(cleanup.subprocess, "run", run)
    result = DockerTarget().clean()
    assert result.freed == 0
    assert "prune exploded" in result.failed[0]


def test_docker_target_is_risky():
    assert DockerTarget().risky is True
