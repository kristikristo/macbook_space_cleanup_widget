"""Pure cleanup-target logic. No rumps imports — trivially testable."""

import json
import os
import shutil
import subprocess
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
            return round(float(value[: -len(unit)]) * _DOCKER_SIZE_UNITS[unit])
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
