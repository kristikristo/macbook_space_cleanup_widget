"""The "Disk Space" app bundle that launchd starts instead of Python.

macOS names a LaunchAgent after the program it starts. Started directly, both
jobs show up as "python" from "Unknown Developer" in Login Items. Started
through Contents/MacOS/disk-space, they show up as "Disk Space".

The launcher also keeps the jobs running across Python upgrades: macOS stores
a code requirement for the program it starts, and Homebrew's ad-hoc signed
Python gets a new identity with each build. The launcher only changes when
launcher/launcher.c or the Info.plist changes.
"""

import plistlib
import subprocess
from pathlib import Path

APP_NAME = "Disk Space"
BUNDLE_ID = "com.user.diskspace"
EXECUTABLE = "disk-space"
APP_PATH = Path.home() / "Applications" / f"{APP_NAME}.app"
SOURCE = Path(__file__).resolve().parent.parent / "launcher" / "launcher.c"


def launcher_path() -> Path:
    return APP_PATH / "Contents" / "MacOS" / EXECUTABLE


def _info_plist() -> bytes:
    return plistlib.dumps(
        {
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleExecutable": EXECUTABLE,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "LSUIElement": True,  # no Dock icon
        }
    )


def _stamp() -> bytes:
    """What the installed launcher was built from."""
    return SOURCE.read_bytes() + _info_plist()


def ensure() -> Path:
    """Build and ad-hoc sign the bundle if it is missing or out of date.

    Returns the launcher path. An unchanged bundle is never rebuilt, so its
    signature, and the code requirement macOS stored for it, stay valid.
    """
    contents = APP_PATH / "Contents"
    stamp_path = contents / "Resources" / "launcher.stamp"
    launcher = launcher_path()
    if launcher.exists() and stamp_path.exists() and stamp_path.read_bytes() == _stamp():
        return launcher

    launcher.parent.mkdir(parents=True, exist_ok=True)
    stamp_path.parent.mkdir(parents=True, exist_ok=True)
    (contents / "Info.plist").write_bytes(_info_plist())
    subprocess.run(
        ["clang", "-O2", "-o", str(launcher), str(SOURCE)],
        check=True,
        capture_output=True,
    )
    stamp_path.write_bytes(_stamp())
    subprocess.run(
        ["codesign", "--force", "--sign", "-", str(APP_PATH)],
        check=True,
        capture_output=True,
    )
    return launcher
