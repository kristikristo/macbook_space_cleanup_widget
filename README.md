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

The 💾 icon appears in your menu bar. Use **Launch at Login** in the dropdown to
start it automatically on boot (it writes a LaunchAgent plist under
`~/Library/LaunchAgents/`).

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
- `src/app.py` — the [rumps](https://github.com/jaredks/rumps) menu bar app; a
  single background worker serializes all scanning and cleaning so the UI stays
  responsive

Design notes live under [`docs/superpowers/`](docs/superpowers/).
