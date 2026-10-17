import plistlib
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src import schedule, weekly_cleanup
from src.cleanup import CleanResult
from src.worktrees import Decision

GiB = 1024**3
MONDAY = datetime(2026, 10, 19, 10, 0)
TUESDAY = datetime(2026, 10, 20, 10, 0)


def test_should_clean_on_monday():
    assert weekly_cleanup.should_clean(MONDAY, 100 * GiB, scheduled=True)


def test_should_not_clean_on_tuesday_with_enough_space():
    assert not weekly_cleanup.should_clean(TUESDAY, 100 * GiB, scheduled=True)


def test_should_clean_any_day_when_space_is_low():
    assert weekly_cleanup.should_clean(TUESDAY, 5 * GiB, scheduled=True)


def test_manual_run_always_cleans():
    assert weekly_cleanup.should_clean(TUESDAY, 100 * GiB, scheduled=False)


def test_summary_reports_freed_and_free_space():
    assert weekly_cleanup.summary(3 * GiB, 50 * GiB) == "Freed 3 GB. 50 GB free."


def test_summary_warns_when_space_is_low():
    message = weekly_cleanup.summary(0, 12 * GiB)
    assert message.startswith("Low disk space: 12 GB free.")


def test_summary_is_none_when_nothing_happened():
    assert weekly_cleanup.summary(0, 50 * GiB) is None


def test_weekly_targets_skip_risky_and_playwright():
    for target in weekly_cleanup.WEEKLY_TARGETS:
        assert not target.risky
        assert not any("playwright" in p for p in target.paths)


class FakeTarget:
    label = "Fake cache"

    def __init__(self):
        self.cleaned = False

    def measure(self):
        return 2 * GiB

    def clean(self):
        self.cleaned = True
        return CleanResult(freed=2 * GiB)


@pytest.fixture
def fake_world(tmp_path, monkeypatch):
    """Patch every side effect of run(); record removals and notifications."""
    done = tmp_path / "done"
    busy = tmp_path / "busy"
    decisions = {
        done: Decision(done, True, "PR #4 merged", "worktree"),
        busy: Decision(busy, False, "in use by a running process", "worktree"),
    }
    world = {"removed": [], "notified": [], "target": FakeTarget()}
    w = weekly_cleanup.worktrees
    monkeypatch.setattr(w, "discover", lambda: [done, busy])
    monkeypatch.setattr(w, "fetch", lambda paths: [])
    monkeypatch.setattr(w, "process_cwds", lambda: ())
    monkeypatch.setattr(w, "evaluate", lambda path, **_: decisions[path])
    monkeypatch.setattr(w, "with_size", lambda d: Decision(d.path, d.remove, d.reason, d.kind, size=GiB))
    monkeypatch.setattr(w, "remove", lambda d: world["removed"].append(d.path))
    monkeypatch.setattr(w, "remove_leftovers", lambda apply: [])
    monkeypatch.setattr(weekly_cleanup, "WEEKLY_TARGETS", (world["target"],))
    monkeypatch.setattr(weekly_cleanup, "brew_cleanup", lambda apply, log: None)
    monkeypatch.setattr(weekly_cleanup, "get_free_space", lambda: 50 * GiB)
    monkeypatch.setattr(weekly_cleanup, "notify", world["notified"].append)
    world["done"] = done
    return world


def test_dry_run_deletes_nothing_and_does_not_notify(fake_world):
    lines = []
    freed = weekly_cleanup.run(apply=False, scheduled=False, now=MONDAY, log=lines.append)
    assert freed == 3 * GiB
    assert fake_world["removed"] == []
    assert not fake_world["target"].cleaned
    assert fake_world["notified"] == []
    assert any("would remove" in line and "PR #4 merged" in line for line in lines)
    assert any("keep" in line and "in use" in line for line in lines)


def test_apply_removes_finished_worktrees_and_notifies(fake_world):
    weekly_cleanup.run(apply=True, scheduled=True, now=MONDAY, log=lambda _line: None)
    assert fake_world["removed"] == [fake_world["done"]]
    assert fake_world["target"].cleaned
    assert fake_world["notified"] == ["Freed 3 GB. 50 GB free."]


def test_scheduled_run_on_tuesday_cleans_nothing(fake_world):
    freed = weekly_cleanup.run(apply=True, scheduled=True, now=TUESDAY, log=lambda _line: None)
    assert freed == 0
    assert fake_world["removed"] == []
    assert fake_world["notified"] == []


def test_low_space_alert_on_a_day_without_cleanup(fake_world, monkeypatch):
    monkeypatch.setattr(weekly_cleanup, "get_free_space", lambda: 30 * GiB)
    monkeypatch.setattr(weekly_cleanup, "LOW_SPACE", 40 * GiB)
    weekly_cleanup.run(apply=True, scheduled=True, now=TUESDAY, log=lambda _line: None)
    # Below LOW_SPACE, the job cleans on any day and then still warns.
    assert fake_world["removed"] == [fake_world["done"]]
    assert fake_world["notified"][0].startswith("Freed 3 GB. Low disk space: 30 GB free.")


@pytest.fixture
def tmp_plist(tmp_path, monkeypatch):
    plist = tmp_path / "com.user.diskcleanup.plist"
    monkeypatch.setattr(schedule, "PLIST_PATH", plist)
    monkeypatch.setattr(schedule.app_bundle, "ensure", lambda: None)  # never build into ~/Applications
    return plist


def test_schedule_plist_runs_daily_with_apply_and_scheduled(tmp_plist):
    with patch("src.schedule.subprocess.run") as mock_run:
        schedule.enable()

    data = plistlib.loads(tmp_plist.read_bytes())
    assert data["Label"] == schedule.PLIST_LABEL
    assert data["ProgramArguments"][0].endswith("Disk Space.app/Contents/MacOS/disk-space")
    assert data["ProgramArguments"][2].endswith("src/weekly_cleanup.py")
    assert data["ProgramArguments"][3:] == ["--apply", "--scheduled"]
    assert data["StartCalendarInterval"] == {"Hour": 10, "Minute": 0}
    assert "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]
    assert Path(data["StandardOutPath"]).name == "disk-cleanup.log"
    assert "RunAtLoad" not in data
    unload_call, load_call = mock_run.call_args_list
    assert unload_call.args[0][:2] == ["launchctl", "unload"]
    assert load_call.args[0][:2] == ["launchctl", "load"]


def test_schedule_disable_removes_plist(tmp_plist):
    tmp_plist.write_text("<?xml version='1.0'?><plist/>")
    with patch("src.schedule.subprocess.run"):
        schedule.disable()
    assert not tmp_plist.exists()
    assert not schedule.is_enabled()
