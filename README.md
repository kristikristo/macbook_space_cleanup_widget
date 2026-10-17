# 💾 Disk Space Widget

A small macOS menu bar app that shows free space on your system drive and
cleans up known space hogs with one click.

The free-space number sits in your menu bar (e.g. `💾 52 GB`). Click it for a
breakdown, a **Cleanup** submenu, a manual refresh, and a launch-at-login
toggle.

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

## Cleanup targets

| Target      | What it clears                                                        | Confirms first |
| ----------- | --------------------------------------------------------------------- | -------------- |
| npm cache   | `~/.npm/_cacache`                                                     | no             |
| Dev caches  | pip, pnpm, CocoaPods, Playwright, Cypress, uv caches                  | no             |
| App caches  | Telegram, Spotify, and app-updater caches                            | no             |
| Docker      | `docker system prune -af` — unused images + build cache               | **yes**        |
| Claude VM   | `~/Library/Application Support/Claude/vm_bundles`                     | **yes**        |

All targets regenerate on next use. **Docker volumes are never pruned** — your
database data is safe. Sizes are measured live in the background, so the menu
bar never freezes while scanning.

## Requirements

- macOS
- Python 3.14+ (the cleanup logic uses `shutil.rmtree(onexc=...)`, which
  replaced the old `onerror=` parameter)

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python src/main.py
```

The 💾 icon appears in your menu bar.

## Run at startup

Click 💾 → **Launch at Login** so it shows a checkmark. That's it — the widget
relaunches automatically every time you log in. Click it again to turn it off.

Under the hood this writes a LaunchAgent plist to
`~/Library/LaunchAgents/com.user.diskspacewidget.plist` and registers it with
`launchctl`. The plist records the exact interpreter (`.venv/bin/python`) and
script path, so it always starts with the virtualenv that has `rumps` installed.

Two things worth knowing:

- **Don't move or delete the project folder.** The startup entry hardcodes the
  path to `.venv/bin/python` and `src/main.py`. If you relocate the repo, just
  toggle Launch at Login off and on again to re-point it.
- **It won't auto-restart if it crashes mid-session** (`KeepAlive` is `false`) —
  only at login. That's the intended behavior for a menu bar utility.

## Weekly cleanup

A background job removes finished git worktrees and clears safe caches, so
agent worktrees (about 1 GB each, mostly `node_modules`) do not fill the disk.

```bash
.venv/bin/python src/weekly_cleanup.py            # dry run: prints what it would do
.venv/bin/python src/weekly_cleanup.py --apply    # clean now
.venv/bin/python src/weekly_cleanup.py --install  # run daily at 10:00
.venv/bin/python src/weekly_cleanup.py --uninstall
```

The installed job runs every day at 10:00 (or on wake, if the Mac slept). It
cleans on Mondays, and on any day when free space is below 20 GB. It shows a
macOS notification with the space it freed, and a warning while free space
stays below 20 GB. Each run appends to `~/Library/Logs/disk-cleanup.log`.

It looks for worktrees in `~/Documents/Repositories/*/.claude/worktrees`,
`~/.claude/worktrees` and `~/.codex/worktrees`. It removes a worktree, or a
clone an agent made there, only when **all** of these are true:

- `git status` shows no changes and no untracked files, and a clone has no stash
- no running process has its working directory inside it
- no commit or checkout in the last 7 days
- every commit is in `origin`'s default branch, or belongs to a merged or
  closed pull request whose head is exactly that commit (checked with `gh`)

Everything else is kept, and the log says why. Linked worktrees are removed
with `git worktree remove` (no `--force`), so the branch stays. The job also
clears the npm cache, the App caches and the uv/pip caches, and runs
`brew cleanup -s`. It never prunes Docker, never removes the Claude VM, and
never clears Playwright browsers.

Like Launch at Login, the job records the path of this folder: install it
from the main checkout, not from a worktree.

## Tests

```bash
.venv/bin/python -m pytest
```

The pure logic (`src/disk.py`, `src/cleanup.py`, `src/login_item.py`) is unit
tested. `src/app.py` is thin rumps glue verified by manual smoke test.

## How it works

- `src/disk.py` — disk usage and human-readable byte formatting (pure)
- `src/cleanup.py` — cleanup target registry; `PathTarget` for directory caches,
  `DockerTarget` for Docker prune (pure, no UI imports)
- `src/login_item.py` — launch-at-login via a LaunchAgent plist (pure)
- `src/worktrees.py` — finds finished git worktrees and removes them (pure)
- `src/weekly_cleanup.py` — the daily job and its command line
- `src/schedule.py` — the daily job's LaunchAgent plist (pure)
- `src/app.py` — the [rumps](https://github.com/jaredks/rumps) menu bar app; a
  single background worker serializes all scanning and cleaning so the UI stays
  responsive

Design notes live under [`docs/superpowers/`](docs/superpowers/).
