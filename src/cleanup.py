"""Pure cleanup-target logic. No rumps imports — trivially testable."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

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

        def _collect(_func, failed_path, _exc):
            failed.append(str(failed_path))

        for path in self._expanded():
            if not path.exists():
                continue
            size_before = dir_size(path)
            shutil.rmtree(path, onexc=_collect)
            size_after = dir_size(path) if path.exists() else 0
            freed += size_before - size_after

        return CleanResult(freed=freed, failed=tuple(failed))
