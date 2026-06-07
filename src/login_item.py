"""Launch-at-login management via LaunchAgent plist + launchctl."""

import plistlib
import subprocess
import sys
from pathlib import Path

PLIST_LABEL = "com.user.diskspacewidget"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def is_enabled() -> bool:
    """Return True iff the LaunchAgent plist exists on disk."""
    return PLIST_PATH.exists()


def _render_plist() -> bytes:
    script = str(Path(__file__).resolve().parent / "main.py")
    return plistlib.dumps(
        {
            "Label": PLIST_LABEL,
            "ProgramArguments": [sys.executable, script],
            "RunAtLoad": True,
            "KeepAlive": False,
        }
    )


def enable() -> None:
    """Write the LaunchAgent plist and load it via launchctl.

    Idempotent: if already loaded, unload first (ignoring errors) then load.
    """
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(_render_plist())

    # Unload first (may fail if not loaded — that's fine).
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
