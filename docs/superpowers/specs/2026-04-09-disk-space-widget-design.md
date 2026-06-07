# macOS Menu Bar Disk Space Widget — Design

**Date:** 2026-04-09
**Status:** Approved

## Overview

A small Python menu bar app that displays the free space on the main
system drive (`/`) next to a drive icon in the macOS menu bar. Clicking
the icon opens a dropdown with total/used/free details, a manual
refresh action, a launch-at-login toggle, and quit. The widget refreshes
every 5 minutes.

**Tech stack:** Python 3 + [`rumps`](https://github.com/jaredks/rumps),
which wraps `NSStatusItem`.

## Decisions

These were settled during brainstorming:

| Decision              | Choice                                         |
| --------------------- | ---------------------------------------------- |
| Scope                 | Display-only (no cleanup actions)              |
| Implementation        | Python 3 + `rumps`                             |
| Volume tracked        | Main system drive (`/`) only                   |
| Display format        | Icon + number (e.g., `💾 55 GB`)               |
| Refresh interval      | Every 5 minutes                                |
| Dropdown contents     | Total / Used / Free, Refresh Now, Quit         |
| Launch at login       | Handled in-app via toggle in dropdown          |

## Components

The project is one module with four files, each with a single clear
responsibility:

### `disk.py`
Pure disk-usage logic. No `rumps` imports, trivially testable.

- `get_free_space(path="/") -> int` — returns free bytes via
  `shutil.disk_usage`.
- `get_disk_usage(path="/") -> tuple[int, int, int]` — returns
  `(total, used, free)` in bytes.
- `format_bytes(n: int) -> str` — formats bytes as `"55 GB"` for values
  ≥1 GB, `"477 MB"` below that.

### `login_item.py`
Manages the launch-at-login toggle. No `rumps` imports.

- `is_enabled() -> bool` — checks whether the LaunchAgent plist exists.
- `enable()` — writes `~/Library/LaunchAgents/com.user.diskspacewidget.plist`
  and runs `launchctl load`. Idempotent: unloads first (ignoring errors)
  before loading.
- `disable()` — runs `launchctl unload` and removes the plist.

### `app.py`
The `rumps.App` subclass. The only file that imports `rumps`.

- Sets the menu bar title using the `internaldrive` SF Symbol (or 💾
  fallback) + the formatted free-space string.
- Builds the dropdown: `Total`, `Used`, `Free` (disabled labels),
  separator, `Refresh Now`, `Launch at Login` (checkable), separator,
  `Quit`.
- Wires up a 5-minute `rumps.Timer` that calls `refresh()`.
- `refresh()` reads disk usage, updates the title, and updates the
  dropdown labels.

### `main.py`
Entry point. Instantiates `DiskSpaceApp` and runs it.

**Why this structure:** `disk.py` and `login_item.py` are pure logic and
unit-testable without a menu bar app. `app.py` is thin glue — easy to
eyeball, hard to test automatically.

## Data flow

```
┌─────────────────┐
│  rumps.Timer    │  fires every 300s
│  (every 5 min)  │
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│ disk.get_disk_usage │───▶ shutil.disk_usage("/")
└────────┬────────────┘
         │ (total, used, free) bytes
         ▼
┌─────────────────────┐
│ disk.format_bytes   │───▶ "55 GB"
└────────┬────────────┘
         │ str
         ▼
┌─────────────────────┐
│ app.title  = "💾 55 GB" │
│ app.menu   = updated  │
│   (Total / Used / Free)│
└─────────────────────┘
```

**Triggers that cause a refresh:**
1. App startup (immediate, before the first timer tick).
2. `rumps.Timer` firing every 300 seconds.
3. User clicking "Refresh Now" in the dropdown.

All three call the same `refresh()` method.

**Launch-at-login flow (separate):** When the user clicks the "Launch at
Login" menu item, the handler calls `login_item.enable()` or
`login_item.disable()` depending on current state. On startup, the
menu item's checkmark is initialized from `login_item.is_enabled()`.

## Error handling

- **`shutil.disk_usage("/")` fails** — extremely unlikely, but if it
  raises, the title falls back to `💾 ?` and the dropdown shows
  `Error reading disk` in place of Total/Used/Free. The timer keeps
  running so the next tick can recover.
- **Plist write / `launchctl` failure in login_item** — surface the
  error with `rumps.alert()` and leave the toggle in its previous state.
  No silent failures.
- **`launchctl load` on an already-loaded plist** — `enable()` runs
  `unload` first (ignoring errors) then `load`, making it idempotent.
- **Unexpected exception in the timer callback** — caught and logged to
  stderr; the timer keeps ticking. A transient failure must not freeze
  the displayed number forever.

No retries, no exponential backoff, no persistent state on disk. The
next 5-minute tick is the recovery mechanism for anything transient.

## Testing

- **`disk.py`** — `pytest` unit tests.
  - `format_bytes`: table-driven tests across the GB/MB boundary, zero,
    and typical values.
  - `get_free_space` / `get_disk_usage`: one test each calling with `/`
    and asserting sensible positive values. Not mocking `shutil` — that
    would only test the mock.

- **`login_item.py`** — `pytest` with a `tmp_path` fixture overriding
  the LaunchAgents directory so tests never touch the real
  `~/Library/LaunchAgents`.
  - `enable()` writes a plist at the expected path with expected
    contents.
  - `disable()` removes the plist.
  - `is_enabled()` returns `True` iff the file exists.
  - `launchctl` calls are mocked — we're testing our wrapper, not
    Apple's tool.

- **`app.py`** — no automated tests. Thin glue over `rumps`, which has
  no good testing story. Manual smoke test: run it, see the number,
  click the menu, toggle launch-at-login, quit.

**Test command:** `pytest` from the project root. Target: all tests pass
in under 1 second.

## Out of scope

Explicitly not part of this project (may be added later):

- Multi-volume support / volume picker.
- Cleanup actions (empty Trash, clear caches, etc.).
- Low-space warnings or notifications.
- Packaging as a standalone `.app` bundle.
- Preferences UI for refresh interval.
