"""Daily job: weekly cleanup of finished worktrees and caches, plus a low-space alert.

Without --apply it is a dry run that only prints what it would do.

    .venv/bin/python src/weekly_cleanup.py            # dry run
    .venv/bin/python src/weekly_cleanup.py --apply    # clean now
    .venv/bin/python src/weekly_cleanup.py --install  # run daily at 10:00

The installed job runs with --apply --scheduled: it cleans on Mondays, and on
any day when free space is below LOW_SPACE. It never prunes Docker and never
removes the Claude VM; the menu bar asks before those.
"""

import argparse
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

# Same as main.py: launchctl runs this file directly, with src/ as sys.path[0].
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import schedule, worktrees  # noqa: E402
from src.cleanup import TARGETS, PathTarget  # noqa: E402
from src.disk import format_bytes, get_free_space  # noqa: E402

LOW_SPACE = 20 * 1024**3
CLEAN_WEEKDAY = 0  # Monday

# Safe to clear without asking: each tool downloads again what it needs.
# Playwright browsers are left alone, because UI checks need them offline.
WEEKLY_TARGETS = (
    next(t for t in TARGETS if t.key == "npm"),
    next(t for t in TARGETS if t.key == "app_caches"),
    PathTarget(key="python_caches", label="Python caches", paths=("~/.cache/uv", "~/Library/Caches/pip")),
)

Log = Callable[[str], None]


def should_clean(now: datetime, free: int, scheduled: bool) -> bool:
    if not scheduled:
        return True
    return now.weekday() == CLEAN_WEEKDAY or free < LOW_SPACE


def summary(freed: int, free: int) -> str | None:
    """Notification text, or None when there is nothing to report."""
    parts = []
    if freed > 0:
        parts.append(f"Freed {format_bytes(freed)}.")
    if free < LOW_SPACE:
        parts.append(f"Low disk space: {format_bytes(free)} free. Open the 💾 menu or the log.")
    elif parts:
        parts.append(f"{format_bytes(free)} free.")
    return " ".join(parts) or None


def notify(message: str) -> None:
    script = 'on run argv\ndisplay notification (item 1 of argv) with title "Disk cleanup"\nend run'
    subprocess.run(["osascript", "-e", script, message], capture_output=True, timeout=30)


def clean_worktrees(apply: bool, now: datetime, log: Log) -> int:
    paths = worktrees.discover()
    for error in worktrees.fetch(paths):
        log(f"  fetch failed, checks use the last known GitHub state: {error}")
    cwds = worktrees.process_cwds()
    total = 0
    for path in paths:
        decision = worktrees.evaluate(
            path, now=now.timestamp(), in_use=cwds, pr_lookup=worktrees.github_pr
        )
        if not decision.remove:
            log(f"  keep          {path}: {decision.reason}")
            continue
        decision = worktrees.with_size(decision)
        if apply:
            error = worktrees.remove(decision)
            if error:
                log(f"  FAILED        {path}: {error}")
                continue
        verb = "removed" if apply else "would remove"
        log(f"  {verb:<13} {path} ({format_bytes(decision.size)}): {decision.reason}")
        total += decision.size
    for leftover in worktrees.remove_leftovers(apply=apply):
        log(f"  {'removed' if apply else 'would remove'} empty folder {leftover}")
    return total


def clean_caches(apply: bool, log: Log) -> int:
    total = 0
    for target in WEEKLY_TARGETS:
        if apply:
            result = target.clean()
            size = result.freed
            for path in result.failed:
                log(f"  FAILED        {path}")
        else:
            size = target.measure()
        log(f"  {'cleared' if apply else 'would clear'} {target.label}: {format_bytes(size)}")
        total += size
    return total


def brew_cleanup(apply: bool, log: Log) -> None:
    brew = shutil.which("brew")
    if brew is None:
        return
    if not apply:
        log("  would run brew cleanup -s")
        return
    proc = subprocess.run([brew, "cleanup", "-s"], capture_output=True, text=True, timeout=900)
    lines = (proc.stdout + proc.stderr).strip().splitlines()
    log(f"  brew cleanup -s: {lines[-1] if lines else 'nothing to clean'}")


def run(*, apply: bool, scheduled: bool, now: datetime | None = None, log: Log = print) -> int:
    """One run of the job. Returns the bytes freed (or that would be freed)."""
    now = now or datetime.now()
    free = get_free_space()
    mode = "apply" if apply else "dry run"
    log(f"{now:%Y-%m-%d %H:%M} disk cleanup ({mode}), {format_bytes(free)} free")

    freed = 0
    if should_clean(now, free, scheduled):
        log("Worktrees:")
        freed += clean_worktrees(apply, now, log)
        log("Caches:")
        freed += clean_caches(apply, log)
        brew_cleanup(apply, log)
    else:
        log("Not Monday and enough free space: no cleanup today.")

    free = get_free_space()
    message = summary(freed if apply else 0, free)
    if message:
        log(message)
        if apply:
            notify(message)
    if not apply:
        log(f"Dry run: would free about {format_bytes(freed)}. Nothing was deleted.")
    return freed


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete; without it, only print")
    parser.add_argument("--scheduled", action="store_true", help="clean only on Mondays or when space is low")
    parser.add_argument("--install", action="store_true", help="run this job daily at 10:00 via launchd")
    parser.add_argument("--uninstall", action="store_true", help="remove the daily job")
    args = parser.parse_args(argv)

    if args.install:
        schedule.enable()
        print(f"Installed {schedule.PLIST_PATH}. Log: {schedule.LOG_PATH}")
        return
    if args.uninstall:
        schedule.disable()
        print(f"Removed {schedule.PLIST_PATH}")
        return
    run(apply=args.apply, scheduled=args.scheduled)


if __name__ == "__main__":
    main()
