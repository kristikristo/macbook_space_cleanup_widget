"""Pure disk-usage logic. No rumps imports — trivially testable."""

import shutil

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


def get_disk_usage(path: str = "/") -> tuple[int, int, int]:
    """Return (total, used, free) in bytes for the volume containing path."""
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def get_free_space(path: str = "/") -> int:
    """Return free bytes for the volume containing path."""
    return shutil.disk_usage(path).free
