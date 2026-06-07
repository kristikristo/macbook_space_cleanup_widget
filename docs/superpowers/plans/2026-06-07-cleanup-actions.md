# Cleanup Actions Implementation Plan

> **For Claude:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Cleanup" submenu to the menu bar widget that shows known space hogs with live sizes and cleans them on click.

**Architecture:** New pure module `src/cleanup.py` holds a declarative registry of cleanup targets (`PathTarget` for directory caches, `DockerTarget` for `docker system prune`), mirroring the existing `disk.py`/`login_item.py` pattern. `src/app.py` renders the submenu generically from the registry and runs all measuring/cleaning on a single background worker thread; a 1-second rumps applier timer pushes results into menu titles on the main thread.

**Tech Stack:** Python 3.14, rumps, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-07-cleanup-actions-design.md`

**Environment notes for the implementer:**
- Run all commands from the project root: `~/Documents/Repositories/macbook_space_cleanup_widget`
- Use the project venv: `.venv/bin/python -m pytest` (plain `pytest` may resolve to another interpreter)
- Python is 3.14: `shutil.rmtree` only accepts `onexc=` (the old `onerror=` parameter was removed)
- The existing suite (14 tests) must stay green after every task

---

## Chunk 1: `cleanup.py` — pure logic

### Task 1: `dir_size` helper

**Files:**
- Create: `src/cleanup.py`
- Create: `tests/test_cleanup.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cleanup.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.cleanup'`

- [ ] **Step 3: Write minimal implementation**

Create `src/cleanup.py`:

```python
"""Pure cleanup-target logic. No rumps imports — trivially testable."""

import os
from pathlib import Path


def dir_size(path: Path) -> int:
    """Total size in bytes of all files under path.

    Does not follow symlinks — a link inside a cache dir must never lead
    the walk outside the target. Missing or unreadable paths count as 0.
    """
    total = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += dir_size(Path(entry.path))
                except OSError:
                    continue  # unreadable entry: skip it, keep counting
    except OSError:
        return 0
    return total
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/cleanup.py tests/test_cleanup.py
git commit -m "feat: add dir_size helper for cleanup targets"
```

### Task 2: `CleanResult` and `PathTarget`

**Files:**
- Modify: `src/cleanup.py`
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cleanup.py` (and extend the import):

```python
from src.cleanup import CleanResult, PathTarget, dir_size


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: FAIL with `ImportError: cannot import name 'CleanResult'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/cleanup.py` (new imports at top, classes below `dir_size`):

```python
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class CleanResult:
    """Outcome of one target's clean(): bytes freed + paths that resisted."""

    freed: int
    failed: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathTarget:
    """A cleanup target that is just 'delete these directories'."""

    key: str
    label: str
    paths: tuple[str, ...]  # may contain ~, expanded at use time
    risky: bool = False

    def _expanded(self) -> tuple[Path, ...]:
        return tuple(Path(p).expanduser() for p in self.paths)

    def measure(self) -> int:
        return sum(dir_size(p) for p in self._expanded())

    def clean(self) -> CleanResult:
        freed = 0
        failed: list[str] = []

        for path in self._expanded():
            if not path.exists():
                continue
            size_before = dir_size(path)

            def _collect(_func, failed_path, _exc):
                failed.append(str(failed_path))

            shutil.rmtree(path, onexc=_collect)
            size_after = dir_size(path) if path.exists() else 0
            freed += size_before - size_after

        return CleanResult(freed=freed, failed=tuple(failed))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/cleanup.py tests/test_cleanup.py
git commit -m "feat: add PathTarget with measure/clean and failure reporting"
```

### Task 3: Docker size parsing

**Files:**
- Modify: `src/cleanup.py`
- Modify: `tests/test_cleanup.py`

Docker's `system df --format json` emits one JSON object per line, with
human-formatted decimal sizes (`kB` = 1000). Real captured output:

```
{"Active":"0","Reclaimable":"0B","Size":"0B","TotalCount":"0","Type":"Images"}
{"Active":"3","Reclaimable":"367.9MB (53%)","Size":"682.1MB","TotalCount":"8","Type":"Local Volumes"}
```

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cleanup.py`:

```python
from src.cleanup import _parse_docker_size


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_docker_size'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/cleanup.py`:

```python
_DOCKER_SIZE_UNITS = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}


def _parse_docker_size(text: str) -> int:
    """Parse docker's human sizes: '367.9MB (53%)' -> 367900000, '0B' -> 0.

    Docker uses decimal units (kB = 1000), unlike disk.format_bytes.
    """
    value = text.split(" ")[0]  # drop the '(53%)' suffix
    for unit in ("TB", "GB", "MB", "kB", "B"):  # longest suffix first
        if value.endswith(unit):
            return int(float(value[: -len(unit)]) * _DOCKER_SIZE_UNITS[unit])
    raise ValueError(f"unrecognized docker size: {text!r}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add src/cleanup.py tests/test_cleanup.py
git commit -m "feat: parse docker system df human-readable sizes"
```

### Task 4: `DockerTarget`

**Files:**
- Modify: `src/cleanup.py`
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cleanup.py`. The fake subprocess pattern: replace
`cleanup.subprocess.run` with a stub returning a canned `CompletedProcess`,
and point `_DOCKER_CANDIDATES` at a path that exists (or not). We test our
parsing and error handling, not Docker.

```python
import subprocess

from src import cleanup
from src.cleanup import DockerTarget

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: FAIL with `ImportError: cannot import name 'DockerTarget'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/cleanup.py` (`import json`, `import subprocess` at top):

```python
_DOCKER_CANDIDATES = (
    "/opt/homebrew/bin/docker",
    "/usr/local/bin/docker",
    "/usr/bin/docker",
)


@dataclass(frozen=True)
class DockerTarget:
    """Cleanup target backed by `docker system prune -af`.

    Reclaims unused images and build cache. NEVER volumes — database
    data lives there.
    """

    key: str = "docker"
    label: str = "Docker"
    risky: bool = True

    @staticmethod
    def _binary() -> str | None:
        # GUI apps get a minimal PATH, so probe known install locations.
        for candidate in _DOCKER_CANDIDATES:
            if os.path.exists(candidate):
                return candidate
        return None

    def measure(self) -> int | None:
        """Reclaimable bytes, or None when docker is missing or not running."""
        binary = self._binary()
        if binary is None:
            return None
        try:
            proc = subprocess.run(
                [binary, "system", "df", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None

        total = 0
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row["Type"] == "Local Volumes":
                continue  # we never prune volumes
            total += _parse_docker_size(row["Reclaimable"])
        return total

    def clean(self) -> CleanResult:
        binary = self._binary()
        if binary is None:
            return CleanResult(freed=0, failed=("docker binary not found",))
        before = self.measure() or 0
        try:
            proc = subprocess.run(
                [binary, "system", "prune", "-af"],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CleanResult(freed=0, failed=(f"docker prune: {exc}",))
        if proc.returncode != 0:
            lines = proc.stderr.strip().splitlines()
            tail = lines[-1] if lines else "unknown error"
            return CleanResult(freed=0, failed=(f"docker prune: {tail}",))
        return CleanResult(freed=before)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: 22 passed

- [ ] **Step 5: Commit**

```bash
git add src/cleanup.py tests/test_cleanup.py
git commit -m "feat: add DockerTarget with volume-safe prune"
```

### Task 5: `TARGETS` registry

**Files:**
- Modify: `src/cleanup.py`
- Modify: `tests/test_cleanup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cleanup.py`:

```python
from src.cleanup import TARGETS


def test_targets_keys_are_unique():
    keys = [t.key for t in TARGETS]
    assert len(keys) == len(set(keys))


def test_targets_paths_all_live_under_home():
    # Guard: a registry typo must never point deletion outside $HOME.
    home = Path.home().resolve()
    for target in TARGETS:
        for raw in getattr(target, "paths", ()):
            expanded = Path(raw).expanduser().resolve()
            assert expanded.is_relative_to(home), f"{target.key}: {raw}"


def test_targets_risky_flags_match_spec():
    risky = {t.key for t in TARGETS if t.risky}
    assert risky == {"docker", "claude_vm"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_cleanup.py -v`
Expected: FAIL with `ImportError: cannot import name 'TARGETS'`

- [ ] **Step 3: Write minimal implementation**

Add to the bottom of `src/cleanup.py`:

```python
TARGETS = (
    PathTarget(key="npm", label="npm cache", paths=("~/.npm/_cacache",)),
    PathTarget(
        key="dev_caches",
        label="Dev caches",
        paths=(
            "~/Library/Caches/pip",
            "~/Library/Caches/pnpm",
            "~/Library/Caches/CocoaPods",
            "~/Library/Caches/ms-playwright",
            "~/Library/Caches/Cypress",
            "~/.cache/uv",
        ),
    ),
    PathTarget(
        key="app_caches",
        label="App caches",
        paths=(
            "~/Library/Caches/ru.keepcoder.Telegram",
            "~/Library/Caches/com.spotify.client",
            "~/Library/Caches/com.todesktop.230313mzl4w4u92.ShipIt",
        ),
    ),
    DockerTarget(),
    PathTarget(
        key="claude_vm",
        label="Claude VM",
        paths=("~/Library/Application Support/Claude/vm_bundles",),
        risky=True,
    ),
)
```

- [ ] **Step 4: Run full suite to verify everything passes**

Run: `.venv/bin/python -m pytest -v`
Expected: 39 passed (14 existing + 25 new)

- [ ] **Step 5: Commit**

```bash
git add src/cleanup.py tests/test_cleanup.py
git commit -m "feat: add cleanup target registry"
```

---

## Chunk 2: `app.py` integration

### Task 6: Cleanup submenu, worker thread, applier timer

**Files:**
- Modify: `src/app.py` (full replacement below)

No automated tests for this task — `app.py` is thin rumps glue with no
test story, consistent with the original design. Correctness lives in
`cleanup.py` (tested above); this task is wiring. Manual smoke test in
Task 7.

Threading model (from the spec):
- ONE background worker thread consumes a `queue.Queue` of jobs. Scans
  and cleans are both jobs, so they serialize — a periodic scan can
  never measure a directory mid-delete.
- The worker only writes into locked state (`_sizes`, `_pending_*`
  flags). It NEVER touches rumps objects.
- A 1-second `rumps.Timer` (`_apply_pending`, main thread) drains that
  state into menu titles, alerts, and notifications.
- Menu click callbacks run on the main thread: confirmation dialogs
  happen there, then the job is enqueued.

- [ ] **Step 1: Replace `src/app.py` with the new implementation**

```python
"""rumps glue. The only file that imports rumps."""

import queue
import sys
import threading
import traceback

import rumps

from src import cleanup, disk, login_item

REFRESH_INTERVAL_SECONDS = 5 * 60  # 5 minutes
APPLIER_INTERVAL_SECONDS = 1
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

        # Cleanup submenu, built generically from the target registry.
        self.cleanup_menu = rumps.MenuItem("Cleanup")
        self.clean_all_item = rumps.MenuItem("Clean All")
        self.cleanup_menu.add(self.clean_all_item)
        self.cleanup_menu.add(rumps.separator)
        self.target_items: dict[str, rumps.MenuItem] = {}
        for target in cleanup.TARGETS:
            item = rumps.MenuItem(f"{target.label} — …")
            self.target_items[target.key] = item
            self.cleanup_menu.add(item)

        self.menu = [
            self.total_item,
            self.used_item,
            self.free_item,
            None,  # separator
            self.cleanup_menu,
            None,
            self.refresh_item,
            self.launch_item,
            None,
            # rumps auto-adds Quit
        ]

        # Background worker state. The worker thread only touches these
        # under the lock; rumps objects are only touched on the main
        # thread (click callbacks and the applier timer).
        self._work_q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._sizes: dict[str, int | None] = {}
        self._sizes_dirty = False
        self._disk_dirty = False
        self._pending_alerts: list[str] = []
        self._pending_notes: list[str] = []
        self._cleaning: set[str] = set()
        threading.Thread(target=self._worker_loop, daemon=True).start()

        self.applier = rumps.Timer(self._apply_pending, APPLIER_INTERVAL_SECONDS)
        self.applier.start()
        self.timer = rumps.Timer(self._on_tick, REFRESH_INTERVAL_SECONDS)
        self.timer.start()

        self.refresh()
        self._enqueue_scan()

    # --- Disk numbers (existing behavior) ---

    def _on_tick(self, _sender) -> None:
        self.refresh()
        self._enqueue_scan()

    def on_refresh_clicked(self, _sender) -> None:
        self.refresh()
        self._enqueue_scan()

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

    # --- Background worker (no rumps access in this section) ---

    def _worker_loop(self) -> None:
        while True:
            job = self._work_q.get()
            try:
                job()
            except Exception:
                traceback.print_exc(file=sys.stderr)

    def _scan_job(self) -> None:
        for target in cleanup.TARGETS:
            size = target.measure()
            with self._lock:
                self._sizes[target.key] = size
                self._sizes_dirty = True

    def _clean_job(self, target) -> None:
        result = target.clean()
        with self._lock:
            self._cleaning.discard(target.key)
            if result.failed:
                shown = ", ".join(result.failed[:3])
                if len(result.failed) > 3:
                    shown += f" (+{len(result.failed) - 3} more)"
                self._pending_alerts.append(f"{target.label}: {shown}")
            else:
                self._pending_notes.append(
                    f"Freed {disk.format_bytes(result.freed)} from {target.label}"
                )
            self._disk_dirty = True
        self._scan_job()

    # --- Main-thread side: enqueue + apply ---

    def _enqueue_scan(self) -> None:
        self.cleanup_menu.title = "Cleanup (scanning…)"
        self._work_q.put(self._scan_job)

    def _apply_pending(self, _sender) -> None:
        with self._lock:
            sizes = dict(self._sizes) if self._sizes_dirty else None
            self._sizes_dirty = False
            disk_dirty, self._disk_dirty = self._disk_dirty, False
            alerts, self._pending_alerts = self._pending_alerts, []
            notes, self._pending_notes = self._pending_notes, []
            cleaning = set(self._cleaning)

        if sizes is not None:
            self._render_sizes(sizes, cleaning)
        if disk_dirty:
            self.refresh()
        for note in notes:
            self._notify(note)
        for message in alerts:
            rumps.alert(title="Cleanup problem", message=message)

    def _render_sizes(self, sizes: dict, cleaning: set) -> None:
        # Targets mid-clean are excluded from the header total; the
        # post-clean re-scan corrects it within a second or two.
        total = 0
        for target in cleanup.TARGETS:
            item = self.target_items[target.key]
            if target.key in cleaning:
                continue  # leave its "Cleaning…" title alone
            size = sizes.get(target.key)
            if size is None:
                item.title = f"{target.label} — not running"
                item.set_callback(None)
            elif size == 0:
                item.title = f"{target.label} — nothing to clean"
                item.set_callback(None)
            else:
                item.title = f"{target.label} — {disk.format_bytes(size)}"
                item.set_callback(self._make_clean_handler(target))
                total += size

        if total > 0:
            self.cleanup_menu.title = f"Cleanup ({disk.format_bytes(total)})"
            self.clean_all_item.title = f"Clean All ({disk.format_bytes(total)})"
            self.clean_all_item.set_callback(self.on_clean_all)
        else:
            self.cleanup_menu.title = "Cleanup"
            self.clean_all_item.title = "Clean All"
            self.clean_all_item.set_callback(None)

    # --- Cleanup actions (click callbacks run on the main thread) ---

    def _make_clean_handler(self, target):
        def handler(_sender) -> None:
            if target.risky and not self._confirm(
                f"Clean {target.label}? This may require re-downloading data later."
            ):
                return
            self._start_clean(target)

        return handler

    def on_clean_all(self, _sender) -> None:
        if not self._confirm("Run all cleanups, including Docker and Claude VM?"):
            return
        with self._lock:
            sizes = dict(self._sizes)
            cleaning = set(self._cleaning)
        for target in cleanup.TARGETS:
            if sizes.get(target.key) and target.key not in cleaning:
                self._start_clean(target)

    def _start_clean(self, target) -> None:
        with self._lock:
            if target.key in self._cleaning:
                return  # already running; ignore the double-click
            self._cleaning.add(target.key)
        item = self.target_items[target.key]
        item.title = f"{target.label} — cleaning…"
        item.set_callback(None)
        self._work_q.put(lambda: self._clean_job(target))

    @staticmethod
    def _confirm(message: str) -> bool:
        return (
            rumps.alert(
                title="Confirm cleanup", message=message, ok="Clean", cancel=True
            )
            == 1
        )

    @staticmethod
    def _notify(message: str) -> None:
        # rumps.notification needs a proper app bundle; running as a bare
        # python process it can raise. Fall back to stderr — the menu
        # sizes and free-space title already reflect the result.
        try:
            rumps.notification(title="Disk Cleanup", subtitle="", message=message)
        except Exception:
            print(message, file=sys.stderr)

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

- [ ] **Step 2: Run the full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: 39 passed (app.py has no automated tests; this catches import
errors and regressions in the pure modules)

- [ ] **Step 3: Verify the app imports cleanly**

Run: `.venv/bin/python -c "import src.app; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/app.py
git commit -m "feat: add Cleanup submenu with background scan/clean worker"
```

### Task 7: Manual smoke test

**Files:** none (verification only)

- [ ] **Step 1: Launch the app**

Run: `.venv/bin/python src/main.py`
Expected: 💾 icon appears in the menu bar with free space number.

- [ ] **Step 2: Walk the checklist**

1. Open the dropdown → "Cleanup (scanning…)" appears, then within a few
   seconds shows a real total, e.g. "Cleanup (2 GB)".
2. Open the Cleanup submenu → each target shows a size, "nothing to
   clean", or "Docker — not running" (whichever is true right now).
3. Click a non-risky target with a size (e.g. npm cache) → no dialog,
   item flips to "cleaning…", then to "nothing to clean"; free-space
   title increases; a notification (or stderr line) reports bytes freed.
4. Click Docker or Claude VM (if they show a size) → confirmation dialog
   appears; Cancel does nothing; Clean runs it.
5. Click "Clean All" → one confirmation, all sized targets clean.
6. "Refresh Now" still updates disk numbers and re-scans sizes.
7. Quit works.

- [ ] **Step 3: Report results**

Report any checklist item that failed, with what was observed instead.
If all pass, the feature is done.
