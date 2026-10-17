import plistlib
import subprocess

import pytest

from src import app_bundle


@pytest.fixture
def tmp_app(tmp_path, monkeypatch):
    """Build the bundle in a temp directory, not in ~/Applications."""
    app = tmp_path / "Disk Space.app"
    monkeypatch.setattr(app_bundle, "APP_PATH", app)
    return app


def test_ensure_builds_a_signed_bundle_named_disk_space(tmp_app):
    launcher = app_bundle.ensure()
    assert launcher == tmp_app / "Contents" / "MacOS" / "disk-space"
    info = plistlib.loads((tmp_app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Disk Space"
    assert info["CFBundleExecutable"] == "disk-space"
    assert info["LSUIElement"] is True
    subprocess.run(["codesign", "--verify", str(tmp_app)], check=True)


def test_launcher_runs_the_program_with_its_arguments(tmp_app):
    launcher = app_bundle.ensure()
    proc = subprocess.run([str(launcher), "/bin/echo", "hello", "world"], capture_output=True, text=True)
    assert proc.returncode == 0
    assert proc.stdout == "hello world\n"


def test_launcher_without_program_prints_usage(tmp_app):
    proc = subprocess.run([str(app_bundle.ensure())], capture_output=True, text=True)
    assert proc.returncode == 64
    assert "usage" in proc.stderr


def test_launcher_with_missing_program_fails(tmp_app):
    proc = subprocess.run([str(app_bundle.ensure()), "/nonexistent"], capture_output=True, text=True)
    assert proc.returncode == 127


def test_ensure_does_not_rebuild_an_unchanged_bundle(tmp_app, monkeypatch):
    app_bundle.ensure()
    calls = []
    monkeypatch.setattr(app_bundle.subprocess, "run", lambda *a, **k: calls.append(a))
    app_bundle.ensure()
    assert calls == []


def test_ensure_rebuilds_when_the_stamp_differs(tmp_app):
    launcher = app_bundle.ensure()
    stamp = tmp_app / "Contents" / "Resources" / "launcher.stamp"
    stamp.write_bytes(b"old")
    app_bundle.ensure()
    assert stamp.read_bytes() == app_bundle._stamp()
    assert launcher.exists()
