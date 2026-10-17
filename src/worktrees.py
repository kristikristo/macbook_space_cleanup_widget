"""Find and remove finished git worktrees. No rumps imports — trivially testable.

A worktree (or a stand-alone clone that an agent made) is "finished" when
nothing in it exists only on this disk:

- no uncommitted, untracked or stashed changes,
- no running process has its working directory inside it,
- no activity for MIN_AGE_DAYS,
- every commit is in the default branch on GitHub, or belongs to a merged or
  closed pull request whose head is exactly that commit.

Anything else is kept, with the reason.
"""

import glob
import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from src.cleanup import dir_size

ROOTS = (
    "~/Documents/Repositories/*/.claude/worktrees",
    "~/.claude/worktrees",
    "~/.codex/worktrees",
)

MIN_AGE_DAYS = 7

# Files that a coding agent leaves in a worktree's wrapper folder.
_WRAPPER_FILES = {".codex-worktree-name", ".DS_Store"}

# Never wait for a password or a host-key prompt in a background job.
_GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15",
}


@dataclass(frozen=True)
class PullRequest:
    number: int
    state: str  # OPEN, MERGED or CLOSED
    head_sha: str


@dataclass(frozen=True)
class Decision:
    path: Path
    remove: bool
    reason: str
    kind: str = ""  # "worktree", "clone", or "" when not a git repository
    common_dir: Path | None = None
    size: int = 0


PrLookup = Callable[[Path, str], PullRequest | None]


def _git(path: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_GIT_ENV,
    )


def _out(path: Path, *args: str) -> str | None:
    proc = _git(path, *args)
    return proc.stdout.strip() if proc.returncode == 0 else None


def discover(roots: Iterable[str] = ROOTS) -> list[Path]:
    """Folders with a .git entry, one or two levels below each root.

    Codex nests a repository inside a wrapper folder (root/<name>/<repo>),
    Claude Code puts it directly below the root (root/<name>).
    """
    found: list[Path] = []
    for pattern in roots:
        for root in sorted(glob.glob(os.path.expanduser(pattern))):
            for child in sorted(Path(root).iterdir()):
                if not child.is_dir() or child.is_symlink():
                    continue
                if (child / ".git").exists():
                    found.append(child)
                    continue
                for grandchild in sorted(child.iterdir()):
                    if grandchild.is_dir() and (grandchild / ".git").exists():
                        found.append(grandchild)
    return found


def process_cwds() -> tuple[str, ...]:
    """Working directories of all running processes of this user."""
    try:
        proc = subprocess.run(
            ["lsof", "-n", "-d", "cwd", "-Fn"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    return tuple(line[1:] for line in proc.stdout.splitlines() if line.startswith("n"))


def _in_use(path: Path, cwds: Iterable[str]) -> bool:
    prefix = str(path) + os.sep
    return any(cwd == str(path) or cwd.startswith(prefix) for cwd in cwds)


def _last_activity(path: Path, git_dir: Path) -> float:
    """Newest of the HEAD commit time and the last checkout/commit in the reflog."""
    times = []
    committed = _out(path, "log", "-1", "--format=%ct")
    if committed:
        times.append(float(committed))
    for name in ("logs/HEAD", "HEAD"):
        try:
            times.append((git_dir / name).stat().st_mtime)
        except OSError:
            continue
    return max(times, default=0.0)


def _default_ref(path: Path) -> str | None:
    target = _out(path, "symbolic-ref", "refs/remotes/origin/HEAD")
    if target:
        return target
    for ref in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
        if _git(path, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return None


def _tips(path: Path, kind: str) -> list[tuple[str | None, str]]:
    """(branch, sha) pairs that must be safe before the folder can go.

    A linked worktree only owns its HEAD; its other branches live in the main
    checkout. A clone owns every local branch.
    """
    head = _out(path, "rev-parse", "HEAD")
    branch = _out(path, "branch", "--show-current") or None
    if head is None:
        return []
    if kind == "worktree":
        return [(branch, head)]
    tips: list[tuple[str | None, str]] = []
    listing = _out(path, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads") or ""
    for line in listing.splitlines():
        name, sha = line.rsplit(" ", 1)
        tips.append((name, sha))
    if branch is None:
        tips.append((None, head))
    return tips


def _tip_finished(path: Path, branch: str | None, sha: str, default: str | None, pr_lookup: PrLookup) -> str | None:
    """Why this commit is safe on GitHub, or None when it is not."""
    if default and _git(path, "merge-base", "--is-ancestor", sha, default).returncode == 0:
        return f"in {default.removeprefix('refs/remotes/')}"
    if branch:
        pr = pr_lookup(path, branch)
        if pr and pr.state in ("MERGED", "CLOSED") and pr.head_sha == sha:
            return f"PR #{pr.number} {pr.state.lower()}"
    return None


def evaluate(
    path: Path,
    *,
    now: float,
    in_use: Iterable[str],
    pr_lookup: PrLookup,
    min_age_days: int = MIN_AGE_DAYS,
) -> Decision:
    """Decide whether path can be removed. Never changes anything."""
    if _in_use(path, in_use):
        return Decision(path, False, "in use by a running process")

    git_dir = _out(path, "rev-parse", "--absolute-git-dir")
    common = _out(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if git_dir is None or common is None:
        return Decision(path, False, "not a git repository")
    kind = "clone" if Path(git_dir) == Path(common) else "worktree"
    keep = lambda reason: Decision(path, False, reason, kind, Path(common))  # noqa: E731

    status = _git(path, "status", "--porcelain")
    if status.returncode != 0:
        return keep("git status failed")
    if status.stdout.strip():
        return keep("has uncommitted or untracked files")
    if kind == "clone" and _out(path, "stash", "list"):
        return keep("has stashed changes")

    if now - _last_activity(path, Path(git_dir)) < min_age_days * 86400:
        return keep(f"active in the last {min_age_days} days")

    tips = _tips(path, kind)
    if not tips:
        return keep("has no commits")
    default = _default_ref(path)
    reasons = []
    for branch, sha in tips:
        why = _tip_finished(path, branch, sha, default, pr_lookup)
        if why is None:
            return keep(f"{branch or sha[:7]}: not in the default branch and no merged or closed PR")
        reasons.append(why)
    return Decision(path, True, ", ".join(sorted(set(reasons))), kind, Path(common))


def github_pr(path: Path, branch: str) -> PullRequest | None:
    """The pull request for branch whose head matches this checkout, via gh."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "list", "--head", branch, "--state", "all", "--limit", "20",
             "--json", "number,state,headRefOid"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        rows = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    head = _out(path, "rev-parse", branch)
    matching = [r for r in rows if r.get("headRefOid") == head] or rows
    # A merged PR wins over a closed or open one for the same branch.
    order = {"MERGED": 0, "CLOSED": 1, "OPEN": 2}
    matching.sort(key=lambda r: order.get(r.get("state"), 3))
    if not matching:
        return None
    best = matching[0]
    return PullRequest(number=best["number"], state=best["state"], head_sha=best["headRefOid"])


def fetch(paths: Iterable[Path]) -> list[str]:
    """Fetch origin once per repository, so merge checks see GitHub's state.

    Returns error lines. A failed fetch only makes the checks stricter.
    """
    errors = []
    seen = set()
    for path in paths:
        common = _out(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if common is None or common in seen:
            continue
        seen.add(common)
        try:
            proc = _git(path, "fetch", "--quiet", "origin", timeout=120)
        except subprocess.TimeoutExpired:
            errors.append(f"{common}: fetch timed out")
            continue
        if proc.returncode != 0:
            lines = proc.stderr.strip().splitlines()
            errors.append(f"{common}: {lines[-1] if lines else 'fetch failed'}")
    return errors


def remove(decision: Decision) -> str | None:
    """Delete a folder that evaluate() approved. Returns an error, or None."""
    if not decision.remove:
        raise ValueError(f"not approved for removal: {decision.path}")
    if decision.kind == "worktree":
        # No --force: git refuses again if something changed since evaluate().
        proc = subprocess.run(
            ["git", "--git-dir", str(decision.common_dir), "worktree", "remove", str(decision.path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            return proc.stderr.strip() or "git worktree remove failed"
        return None
    failed: list[str] = []
    shutil.rmtree(decision.path, onexc=lambda _f, p, _e: failed.append(str(p)))
    return f"could not delete {len(failed)} files" if failed else None


def remove_leftovers(roots: Iterable[str] = ROOTS, *, apply: bool) -> list[Path]:
    """Wrapper folders that are empty or hold only an agent's name file."""
    leftovers = []
    for pattern in roots:
        for root in sorted(glob.glob(os.path.expanduser(pattern))):
            for child in sorted(Path(root).iterdir()):
                if not child.is_dir() or child.is_symlink():
                    continue
                if {entry.name for entry in child.iterdir()} <= _WRAPPER_FILES:
                    leftovers.append(child)
                    if apply:
                        shutil.rmtree(child, ignore_errors=True)
    return leftovers


def with_size(decision: Decision) -> Decision:
    return Decision(
        decision.path, decision.remove, decision.reason, decision.kind, decision.common_dir, dir_size(decision.path)
    )
