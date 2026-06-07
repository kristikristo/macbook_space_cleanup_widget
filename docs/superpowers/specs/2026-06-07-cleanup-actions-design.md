# Cleanup Actions — Design

**Date:** 2026-06-07
**Status:** Approved

## Overview

Add one-click cleanup actions to the existing menu bar disk space widget.
A "Cleanup" submenu lists known space hogs with their current sizes
(e.g., "npm cache — 6 GB"); clicking one cleans it, and "Clean All"
runs everything. Risky targets confirm first. This was explicitly listed
as "out of scope, may be added later" in the original 2026-04-09 design.

## Decisions

Settled during brainstorming:

| Decision        | Choice                                                  |
| --------------- | ------------------------------------------------------- |
| Targets         | Dev caches, npm cache, Docker prune, Claude VM, app caches |
| Menu UX         | "Cleanup" submenu with per-item sizes + Clean All       |
| Confirmation    | Risky only: Docker, Claude VM, Clean All                |
| Architecture    | Declarative target registry in a new pure module        |
| Docker volumes  | Never pruned — database data is sacred                  |
| Size formatting | Reuse `disk.format_bytes` (whole units, e.g. "6 GB")    |

## Menu layout

```
💾 52 GB
├ Total: 228 GB
├ Used:  176 GB
├ Free:   52 GB
├ ─────────────
├ Cleanup (18 GB) ▸
│   ├ Clean All (18 GB)
│   ├ ───────────
│   ├ npm cache — 6 GB
│   ├ Docker — 10 GB
│   ├ Claude VM — 10 GB
│   ├ Dev caches — 2 GB
│   └ App caches — 2 GB
├ ─────────────
├ Refresh Now
├ Launch at Login
└ Quit
```

## Components

### `cleanup.py` (new — pure logic, no rumps imports)

Mirrors the `disk.py` / `login_item.py` pattern: testable without a
menu bar app.

- **`dir_size(path) -> int`** — standalone helper: recursive
  `os.scandir` walk summing file sizes. Does **not** follow symlinks
  (a link inside a cache dir must never lead the walk — or a later
  deletion — outside the target). Missing path → 0.
- **`PathTarget`** dataclass: `key`, `label`, `paths` (tuple of
  `~`-expanded directories), `risky: bool`.
  - `measure() -> int` — sum of `dir_size()` over `paths`.
  - `clean() -> CleanResult` — delete each path's contents
    (symlinks removed, never followed), return bytes actually freed
    plus any paths that failed.
- **`DockerTarget`** — same interface, different internals.
  - Finds the `docker` binary across known locations
    (`/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`) because GUI apps
    get a minimal `PATH`.
  - `measure() -> int | None` — parses `docker system df --format json`
    and sums reclaimable bytes; `None` when the binary is missing or
    the daemon isn't running.
  - `clean()` — `docker system prune -af` (unused images + build cache).
    **Never volumes.**
- **`TARGETS`** registry:

| Key          | Label      | Source                                                                 | Risky |
| ------------ | ---------- | ---------------------------------------------------------------------- | ----- |
| `npm`        | npm cache  | `~/.npm/_cacache`                                                       | no    |
| `dev_caches` | Dev caches | `~/Library/Caches/{pip, pnpm, CocoaPods, ms-playwright, Cypress}`, `~/.cache/uv` | no    |
| `app_caches` | App caches | `~/Library/Caches/{ru.keepcoder.Telegram, com.spotify.client, com.todesktop.230313mzl4w4u92.ShipIt}` | no    |
| `docker`     | Docker     | `docker system df` / `docker system prune -af`                          | yes   |
| `claude_vm`  | Claude VM  | `~/Library/Application Support/Claude/vm_bundles`                       | yes   |

### `app.py` (modified — stays thin)

- Builds the Cleanup submenu generically from `cleanup.TARGETS`; it
  never knows what the targets are, only how to render them.
- Background daemon thread runs all `measure()` and `clean()` calls —
  scanning a 6 GB npm cache takes seconds and Docker prune can take a
  minute; the menu bar must never freeze.
- Worker writes results into a thread-safe store; a lightweight
  1-second rumps applier timer pushes pending updates into menu titles
  on the main thread.

## Data flow

```
startup / 5-min tick / Refresh Now / post-clean
        │
        ▼
┌──────────────────────┐      ┌─────────────────────────┐
│ scan worker (thread) │────▶│ thread-safe result store │
│ target.measure() ×5  │      └───────────┬─────────────┘
└──────────────────────┘                  │ dirty flag
                                          ▼
                              ┌─────────────────────────┐
                              │ 1s applier rumps.Timer   │
                              │ (main thread)            │
                              │ updates submenu titles   │
                              └─────────────────────────┘

click target ──▶ risky? ──confirm──▶ clean worker (thread)
                                      │ item: "Cleaning…", disabled
                                      ▼
                              notification "Freed 6 GB"
                              → re-scan + disk refresh
```

While a scan is in flight the submenu reads "Cleanup (scanning…)".

**Scan/clean overlap:** a single worker queue serializes all background
work — a periodic scan never runs while a clean is mid-delete (it would
measure a directory being emptied and flash a junk number). Whatever is
queued runs next; results land in the store in order.

## Error handling

- **Target measures 0 / path missing** — item shows
  "npm cache — nothing to clean", disabled. The normal post-clean state,
  not an error.
- **Docker unavailable** — "Docker — not running", disabled. Re-checked
  every scan, so starting Docker Desktop lights it up within a tick.
- **Partial deletion failure** (file held open, permission denied) —
  delete what we can, count actual bytes freed, surface one
  `rumps.alert` naming the failed paths. No silent failures; one
  stubborn file never aborts the rest.
- **`docker prune` non-zero exit** — alert with the stderr tail.
- **Double-click protection** — items disabled while their cleanup
  runs; "Clean All" during an in-flight clean is ignored.
- Recovery model unchanged from the original design: no retries, no
  persisted state — the next scan tick self-corrects.

## Testing

`tests/test_cleanup.py` — pytest, `tmp_path`-based, fast:

- `dir_size`: empty dir, nested files, missing path → 0.
- `PathTarget.measure/clean`: fake cache dirs in `tmp_path`; verify
  byte counts, contents removed, freed bytes returned; partial-failure
  case (read-only file) reports failures without raising.
- `DockerTarget`: subprocess mocked — parse real captured
  `docker system df --format json` output; missing binary → `None`;
  non-zero exit → `None`. We test our parsing, not Docker.
- Registry sanity: every target path lives under `$HOME`, keys unique —
  a guard against a typo ever pointing deletion somewhere catastrophic.

`app.py` menu glue — no automated tests (rumps has no test story,
consistent with the original design). Manual smoke test: scan populates
sizes, clean npm cache, confirm dialog on Docker, notification appears,
title refreshes.

## Out of scope

- Scheduling automatic cleanups.
- Configurable target list / preferences UI.
- Trash emptying, system-level caches (require elevated permissions).
- Low-space warnings.
