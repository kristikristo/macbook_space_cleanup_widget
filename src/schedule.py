"""Daily cleanup job via a LaunchAgent plist + launchctl."""

import plistlib
import subprocess
import sys
from pathlib import Path

from src import app_bundle

PLIST_LABEL = "com.user.diskcleanup"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
LOG_PATH = Path.home() / "Library" / "Logs" / "disk-cleanup.log"
RUN_HOUR = 10

# launchd starts jobs with a minimal PATH; git, gh, brew and lsof live here.
_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def is_enabled() -> bool:
    """Return True iff the LaunchAgent plist exists on disk."""
    return PLIST_PATH.exists()


def _render_plist() -> bytes:
    script = str(Path(__file__).resolve().parent / "weekly_cleanup.py")
    return plistlib.dumps(
        {
            "Label": PLIST_LABEL,
            "ProgramArguments": [str(app_bundle.launcher_path()), sys.executable, script, "--apply", "--scheduled"],
            # launchd runs a missed time when the Mac wakes up.
            "StartCalendarInterval": {"Hour": RUN_HOUR, "Minute": 0},
            "EnvironmentVariables": {"PATH": _PATH},
            "StandardOutPath": str(LOG_PATH),
            "StandardErrorPath": str(LOG_PATH),
            "LowPriorityIO": True,
            "Nice": 10,
        }
    )


def enable() -> None:
    """Write the LaunchAgent plist and load it via launchctl.

    Idempotent: if already loaded, unload first (ignoring errors) then load.
    """
    app_bundle.ensure()
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(_render_plist())

    try:
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass

    subprocess.run(
        ["launchctl", "load", str(PLIST_PATH)],
        check=True,
        capture_output=True,
    )


def disable() -> None:
    """Unload the LaunchAgent and remove its plist. Safe if already gone."""
    if not PLIST_PATH.exists():
        return

    try:
        subprocess.run(
            ["launchctl", "unload", str(PLIST_PATH)],
            check=False,
            capture_output=True,
        )
    except Exception:
        pass

    PLIST_PATH.unlink(missing_ok=True)
