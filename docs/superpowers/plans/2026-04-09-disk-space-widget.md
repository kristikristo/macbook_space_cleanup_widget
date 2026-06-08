# Disk Space Widget Implementation Plan

> **For Claude:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a macOS menu bar widget in Python that displays free space on the main drive (`/`) with icon, updates every 5 minutes, and offers a dropdown with details, manual refresh, and a launch-at-login toggle.

**Architecture:** Single Python project with four source files — `disk.py` (pure usage logic), `login_item.py` (LaunchAgent plist management), `app.py` (`rumps.App` subclass gluing everything together), and `main.py` (entry point). Unit tests use `pytest` for the two pure-logic modules; `app.py` is verified via manual smoke test.

**Tech Stack:** Python 3, `rumps` (menu bar app framework), `pytest` (tests), `shutil.disk_usage` (disk info), `launchctl` + LaunchAgent plist (launch-at-login).

**Spec:** `docs/superpowers/specs/2026-04-09-disk-space-widget-design.md`

**Note:** The user has opted out of git for this project. Do NOT run `git init`, `git add`, or `git commit`. No commit steps are included below.

---

## File Structure

```
macbook_space_cleanup_widget/
├── requirements.txt           # rumps, pytest
├── src/
│   ├── __init__.py
│   ├── disk.py                # get_free_space, get_disk_usage, format_bytes
│   ├── login_item.py          # is_enabled, enable, disable
│   ├── app.py                 # DiskSpaceApp(rumps.App)
│   └── main.py                # entry point
└── tests/
    ├── __init__.py
    ├── test_disk.py           # unit tests for disk.py
    └── test_login_item.py     # unit tests for login_item.py
```

---

## Task 1: Project Setup

**Files:**
- Create: `requirements.txt`
- Create: `src/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create `requirements.txt`**

```
rumps>=0.4.0
pytest>=7.0
```

- [ ] **Step 2: Create empty package files**

Create `src/__init__.py` and `tests/__init__.py` as empty files.

- [ ] **Step 3: Create and populate a virtual environment**

Run:
```bash
cd ~/Documents/Repositories/macbook_space_cleanup_widget
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Expected: `pip install` completes with no errors. `rumps` and `pytest` both install.

- [ ] **Step 4: Verify pytest can run (no tests yet)**

Run: `.venv/bin/pytest tests/ -v`
Expected: `no tests ran` (exit code 5 is fine — means no tests collected).

---

## Task 2: `disk.format_bytes` (TDD)

**Files:**
- Create: `src/disk.py`
- Create: `tests/test_disk.py`

- [ ] **Step 1: Write failing tests for `format_bytes`**

Create `tests/test_disk.py`:

```python
import pytest

from src.disk import format_bytes


@pytest.mark.parametrize(
    "n_bytes, expected",
    [
        (0, "0 MB"),
        (500 * 1024 * 1024, "500 MB"),           # 500 MiB
        (999 * 1024 * 1024, "999 MB"),           # just under 1 GiB
        (1024 * 1024 * 1024, "1 GB"),            # exactly 1 GiB
        (55 * 1024 * 1024 * 1024, "55 GB"),      # 55 GiB
        (1536 * 1024 * 1024, "2 GB"),            # rounding up to nearest GB
    ],
)
def test_format_bytes(n_bytes, expected):
    assert format_bytes(n_bytes) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_disk.py -v`
Expected: FAIL with `ImportError: cannot import name 'format_bytes' from 'src.disk'` (or `ModuleNotFoundError` if `src/disk.py` doesn't exist yet).

- [ ] **Step 3: Implement `format_bytes`**

Create `src/disk.py`:

```python
"""Pure disk-usage logic. No rumps imports — trivially testable."""

_MiB = 1024 * 1024
_GiB = 1024 * _MiB


def format_bytes(n: int) -> str:
    """Format bytes as a human-readable string using binary units.

    Uses GB for values >= 1 GiB, MB otherwise. Rounds to the nearest
    whole unit.
    """
    if n >= _GiB:
        return f"{round(n / _GiB)} GB"
    return f"{round(n / _MiB)} MB"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_disk.py -v`
Expected: all 6 parameterized cases PASS.

---

## Task 3: `disk.get_disk_usage` and `get_free_space` (TDD)

**Files:**
- Modify: `src/disk.py`
- Modify: `tests/test_disk.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_disk.py`:

```python
from src.disk import get_disk_usage, get_free_space


def test_get_free_space_returns_positive_int():
    free = get_free_space("/")
    assert isinstance(free, int)
    assert free > 0


def test_get_disk_usage_returns_total_used_free():
    total, used, free = get_disk_usage("/")
    assert isinstance(total, int) and total > 0
    assert isinstance(used, int) and used >= 0
    assert isinstance(free, int) and free >= 0
    # Used + free should be within a small epsilon of total (filesystems
    # reserve some space, so they won't match exactly).
    assert used + free <= total
    assert used + free >= total * 0.8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_disk.py -v`
Expected: the two new tests FAIL with `ImportError`.

- [ ] **Step 3: Implement the functions**

Append to `src/disk.py`:

```python
import shutil


def get_disk_usage(path: str = "/") -> tuple[int, int, int]:
    """Return (total, used, free) in bytes for the volume containing path."""
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def get_free_space(path: str = "/") -> int:
    """Return free bytes for the volume containing path."""
    return shutil.disk_usage(path).free
```

Move the `import shutil` to the top of the file (standard position).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_disk.py -v`
Expected: all tests PASS.

---

## Task 4: `login_item.is_enabled` (TDD)

**Files:**
- Create: `src/login_item.py`
- Create: `tests/test_login_item.py`

Context: `login_item.py` needs to write/remove a LaunchAgent plist at
`~/Library/LaunchAgents/com.user.diskspacewidget.plist`. To keep tests
isolated from the real home directory, the module exposes a module-level
`PLIST_PATH` that tests can monkeypatch.

- [ ] **Step 1: Write failing test for `is_enabled`**

Create `tests/test_login_item.py`:

```python
import pytest

from src import login_item


@pytest.fixture
def tmp_plist(tmp_path, monkeypatch):
    """Redirect the plist path to a temp directory."""
    plist = tmp_path / "com.user.diskspacewidget.plist"
    monkeypatch.setattr(login_item, "PLIST_PATH", plist)
    return plist


def test_is_enabled_false_when_plist_missing(tmp_plist):
    assert login_item.is_enabled() is False


def test_is_enabled_true_when_plist_present(tmp_plist):
    tmp_plist.write_text("<?xml version='1.0'?><plist/>")
    assert login_item.is_enabled() is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_login_item.py -v`
Expected: FAIL with `ImportError` or `AttributeError: module 'src.login_item' has no attribute 'PLIST_PATH'`.

- [ ] **Step 3: Implement `PLIST_PATH` and `is_enabled`**

Create `src/login_item.py`:

```python
"""Launch-at-login management via LaunchAgent plist + launchctl."""

from pathlib import Path

PLIST_LABEL = "com.user.diskspacewidget"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def is_enabled() -> bool:
    """Return True iff the LaunchAgent plist exists on disk."""
    return PLIST_PATH.exists()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_login_item.py -v`
Expected: both tests PASS.

---

## Task 5: `login_item.enable` (TDD)

**Files:**
- Modify: `src/login_item.py`
- Modify: `tests/test_login_item.py`

Context: `enable()` must (1) ensure the parent directory exists,
(2) write the plist with `ProgramArguments` pointing at the currently
running Python interpreter and the `main.py` entry point, (3) run
`launchctl unload` (ignoring errors — plist may not be loaded),
(4) run `launchctl load`. The `subprocess` calls are mocked in tests so
we don't actually touch `launchctl`.

- [ ] **Step 1: Write failing tests for `enable`**

Append to `tests/test_login_item.py`:

```python
from unittest.mock import patch


def test_enable_writes_plist_and_loads(tmp_plist):
    with patch("src.login_item.subprocess.run") as mock_run:
        login_item.enable()

    assert tmp_plist.exists()
    content = tmp_plist.read_text()
    assert "<key>Label</key>" in content
    assert f"<string>{login_item.PLIST_LABEL}</string>" in content
    assert "<key>ProgramArguments</key>" in content
    assert "<key>RunAtLoad</key>" in content
    assert "<true/>" in content

    # Should have called launchctl unload (ignored errors) then load.
    assert mock_run.call_count == 2
    unload_call, load_call = mock_run.call_args_list
    assert unload_call.args[0][:2] == ["launchctl", "unload"]
    assert load_call.args[0][:2] == ["launchctl", "load"]


def test_enable_is_idempotent_when_already_loaded(tmp_plist):
    # unload raises (plist loaded or not) — enable should swallow it and still load
    def run_side_effect(args, **kwargs):
        if args[:2] == ["launchctl", "unload"]:
            raise FileNotFoundError("not loaded")
        return None

    with patch("src.login_item.subprocess.run", side_effect=run_side_effect) as mock_run:
        login_item.enable()

    assert tmp_plist.exists()
    assert mock_run.call_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_login_item.py -v`
Expected: the two new tests FAIL with `AttributeError` (no `enable`) or `ModuleNotFoundError` for `subprocess` attribute.

- [ ] **Step 3: Implement `enable`**

Append to `src/login_item.py`:

```python
import subprocess
import sys
from pathlib import Path


_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python}</string>
        <string>{script}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""


def _render_plist() -> str:
    python = sys.executable
    script = str((Path(__file__).resolve().parent / "main.py"))
    return _PLIST_TEMPLATE.format(label=PLIST_LABEL, python=python, script=script)


def enable() -> None:
    """Write the LaunchAgent plist and load it via launchctl.

    Idempotent: if already loaded, unload first (ignoring errors) then load.
    """
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(_render_plist())

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_login_item.py -v`
Expected: all tests PASS.

---

## Task 6: `login_item.disable` (TDD)

**Files:**
- Modify: `src/login_item.py`
- Modify: `tests/test_login_item.py`

- [ ] **Step 1: Write failing tests for `disable`**

Append to `tests/test_login_item.py`:

```python
def test_disable_removes_plist_and_unloads(tmp_plist):
    tmp_plist.write_text("<?xml version='1.0'?><plist/>")
    with patch("src.login_item.subprocess.run") as mock_run:
        login_item.disable()

    assert not tmp_plist.exists()
    assert mock_run.call_count == 1
    assert mock_run.call_args.args[0][:2] == ["launchctl", "unload"]


def test_disable_is_safe_when_plist_missing(tmp_plist):
    # No plist exists. disable() should not raise.
    with patch("src.login_item.subprocess.run") as mock_run:
        login_item.disable()
    assert not tmp_plist.exists()
    # Nothing to unload, so subprocess.run shouldn't be called.
    assert mock_run.call_count == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_login_item.py -v`
Expected: the two new tests FAIL with `AttributeError: module 'src.login_item' has no attribute 'disable'`.

- [ ] **Step 3: Implement `disable`**

Append to `src/login_item.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_login_item.py -v`
Expected: all tests (6 total in this file) PASS.

- [ ] **Step 5: Run the full test suite**

Run: `.venv/bin/pytest -v`
Expected: all tests across `test_disk.py` and `test_login_item.py` PASS in under 1 second.

---

## Task 7: `app.py` — `DiskSpaceApp` (no automated tests)

**Files:**
- Create: `src/app.py`

Context: `app.py` is the thin glue over `rumps`. It has no unit tests
(rumps has no good test story); verification is the manual smoke test
in Task 9.

Key behaviors:
1. Title shows `💾 <free GB>` (the SF Symbol `internaldrive` is nicer
   but requires an NSImage; the emoji keeps this dependency-free).
2. Dropdown items: Total, Used, Free (disabled labels), separator,
   Refresh Now, Launch at Login (checkable), separator, Quit.
3. `refresh()` is called on startup, on a 5-minute timer, and on
   "Refresh Now" clicks.
4. The Launch at Login menu item's state reflects `login_item.is_enabled()`
   on startup and is toggled via handler.
5. Any exception inside `refresh` is caught; the title becomes `💾 ?`
   and the disk labels become `Error reading disk`.

- [ ] **Step 1: Create `src/app.py`**

```python
"""rumps glue. The only file that imports rumps."""

import sys
import traceback

import rumps

from src import disk, login_item

REFRESH_INTERVAL_SECONDS = 5 * 60  # 5 minutes
DRIVE_EMOJI = "💾"


class DiskSpaceApp(rumps.App):
    def __init__(self) -> None:
        super().__init__(name="DiskSpace", title=f"{DRIVE_EMOJI} …")

        # Dropdown items. Labels without callbacks render as disabled.
        self.total_item = rumps.MenuItem("Total: —")
        self.used_item = rumps.MenuItem("Used: —")
        self.free_item = rumps.MenuItem("Free: —")
        self.refresh_item = rumps.MenuItem(
            "Refresh Now", callback=self.on_refresh_clicked
        )
        self.launch_item = rumps.MenuItem(
            "Launch at Login", callback=self.on_launch_toggled
        )
        self.launch_item.state = login_item.is_enabled()

        self.menu = [
            self.total_item,
            self.used_item,
            self.free_item,
            None,  # separator
            self.refresh_item,
            self.launch_item,
            None,
            # rumps auto-adds Quit
        ]

        self.timer = rumps.Timer(self._on_tick, REFRESH_INTERVAL_SECONDS)
        self.timer.start()

        self.refresh()

    # --- Refresh ---

    def _on_tick(self, _sender) -> None:
        self.refresh()

    def on_refresh_clicked(self, _sender) -> None:
        self.refresh()

    def refresh(self) -> None:
        try:
            total, used, free = disk.get_disk_usage("/")
            self.title = f"{DRIVE_EMOJI} {disk.format_bytes(free)}"
            self.total_item.title = f"Total: {disk.format_bytes(total)}"
            self.used_item.title = f"Used:  {disk.format_bytes(used)}"
            self.free_item.title = f"Free:  {disk.format_bytes(free)}"
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self.title = f"{DRIVE_EMOJI} ?"
            self.total_item.title = "Error reading disk"
            self.used_item.title = ""
            self.free_item.title = ""

    # --- Launch at login ---

    def on_launch_toggled(self, sender) -> None:
        try:
            if sender.state:
                login_item.disable()
                sender.state = False
            else:
                login_item.enable()
                sender.state = True
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            rumps.alert(
                title="Launch at Login failed",
                message=str(exc),
            )
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `.venv/bin/python -c "from src import app; print('ok')"`
Expected: prints `ok` with no traceback.

---

## Task 8: `main.py` Entry Point

**Files:**
- Create: `src/main.py`

- [ ] **Step 1: Create `src/main.py`**

```python
"""Entry point for the disk space menu bar widget."""

from src.app import DiskSpaceApp


def main() -> None:
    DiskSpaceApp().run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the module imports cleanly**

Run: `.venv/bin/python -c "from src import main; print('ok')"`
Expected: prints `ok` with no traceback.

---

## Task 9: Manual Smoke Test

Automated tests can't exercise `rumps`, so verify the full app by hand.

- [ ] **Step 1: Launch the app**

Run: `.venv/bin/python -m src.main`
Expected: a drive emoji and a number like `55 GB` appear in the menu bar. No errors on stderr.

- [ ] **Step 2: Verify the dropdown**

Click the menu bar icon.
Expected: dropdown shows `Total: …`, `Used: …`, `Free: …`, a separator, `Refresh Now`, `Launch at Login` (unchecked on first run), another separator, and `Quit`. The three disk labels show real sizes.

- [ ] **Step 3: Verify manual refresh**

Click `Refresh Now`.
Expected: numbers stay the same (or change slightly if you've written to disk). No errors.

- [ ] **Step 4: Verify Launch at Login toggle — enable**

Click `Launch at Login`.
Expected: the item gets a checkmark. No error dialog. Verify the plist was written:

Run: `ls ~/Library/LaunchAgents/com.user.diskspacewidget.plist`
Expected: file exists.

Run: `launchctl list | grep diskspacewidget`
Expected: a matching line appears.

- [ ] **Step 5: Verify Launch at Login toggle — disable**

Click `Launch at Login` again.
Expected: checkmark disappears. No error dialog. Verify the plist was removed:

Run: `ls ~/Library/LaunchAgents/com.user.diskspacewidget.plist 2>&1`
Expected: `No such file or directory`.

- [ ] **Step 6: Quit the app**

Click `Quit`.
Expected: menu bar icon disappears.

- [ ] **Step 7: Final test run**

Run: `.venv/bin/pytest -v`
Expected: all tests pass in under 1 second.

---

## Done

The widget is functional: installs via `pip install -r requirements.txt`,
runs via `python -m src.main`, and can be configured to launch at login
through its own dropdown.
