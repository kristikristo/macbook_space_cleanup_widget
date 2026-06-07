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
            # Zero immediately so the applier can't re-enable the item with
            # its stale pre-clean size; the re-scan below sets the real value.
            self._sizes[target.key] = 0
            self._sizes_dirty = True
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
