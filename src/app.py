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
