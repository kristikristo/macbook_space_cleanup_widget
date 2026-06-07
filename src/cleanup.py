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
