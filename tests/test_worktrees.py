import os
import subprocess
import time
from pathlib import Path

import pytest

from src import worktrees
from src.worktrees import Decision, PullRequest, discover, evaluate, remove, remove_leftovers

LATER = time.time() + 30 * 86400  # "now" for checks that need an old worktree

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=_GIT_ENV, capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def commit(cwd: Path, name: str) -> str:
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-qm", name)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    """A clone of a bare 'GitHub' remote, with one commit on main."""
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-qb", "main")
    commit(seed, "readme")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote))
    clone = tmp_path / "repo"
    git(tmp_path, "clone", "-q", str(remote), str(clone))
    return clone


def add_worktree(repo: Path, root: Path, name: str) -> Path:
    path = root / name
    git(repo, "worktree", "add", "-q", "-b", f"claude/{name}", str(path), "origin/main")
    return path


def no_pr(_path, _branch):
    return None


def check(path, *, now=LATER, in_use=(), pr_lookup=no_pr):
    return evaluate(path, now=now, in_use=in_use, pr_lookup=pr_lookup)


def test_worktree_merged_into_main_is_removed(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "done")
    decision = check(wt)
    assert decision.remove
    assert decision.kind == "worktree"
    assert "main" in decision.reason


def test_worktree_with_uncommitted_change_is_kept(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "dirty")
    (wt / "readme").write_text("changed")
    decision = check(wt)
    assert not decision.remove
    assert "uncommitted" in decision.reason


def test_worktree_with_untracked_file_is_kept(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "untracked")
    (wt / "notes.txt").write_text("draft")
    assert not check(wt).remove


def test_worktree_used_by_a_process_is_kept(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "busy")
    decision = check(wt, in_use=(str(wt / "apps" / "web"),))
    assert not decision.remove
    assert "in use" in decision.reason


def test_process_in_a_sibling_with_the_same_prefix_does_not_count(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "a")
    assert check(wt, in_use=(str(wt) + "-other",)).remove


def test_recent_worktree_is_kept(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "fresh")
    decision = check(wt, now=time.time())
    assert not decision.remove
    assert "7 days" in decision.reason


def test_unmerged_commit_without_pr_is_kept(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "feature")
    commit(wt, "work")
    decision = check(wt)
    assert not decision.remove
    assert "claude/feature" in decision.reason


def test_unmerged_commit_with_merged_pr_on_same_commit_is_removed(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "squashed")
    sha = commit(wt, "work")

    def lookup(_path, branch):
        assert branch == "claude/squashed"
        return PullRequest(number=12, state="MERGED", head_sha=sha)

    decision = check(wt, pr_lookup=lookup)
    assert decision.remove
    assert "#12" in decision.reason


def test_closed_pr_on_same_commit_is_removed(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "closed")
    sha = commit(wt, "work")
    lookup = lambda _p, _b: PullRequest(number=3, state="CLOSED", head_sha=sha)  # noqa: E731
    assert check(wt, pr_lookup=lookup).remove


def test_merged_pr_on_older_commit_is_kept(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "more-work")
    old = commit(wt, "work")
    commit(wt, "later")
    lookup = lambda _p, _b: PullRequest(number=5, state="MERGED", head_sha=old)  # noqa: E731
    assert not check(wt, pr_lookup=lookup).remove


def test_open_pr_is_kept(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "open")
    sha = commit(wt, "work")
    lookup = lambda _p, _b: PullRequest(number=7, state="OPEN", head_sha=sha)  # noqa: E731
    assert not check(wt, pr_lookup=lookup).remove


def test_clean_clone_on_main_is_removed(repo):
    decision = check(repo)
    assert decision.remove
    assert decision.kind == "clone"


def test_clone_with_stash_is_kept(repo):
    (repo / "readme").write_text("changed")
    git(repo, "stash", "-q")
    decision = check(repo)
    assert not decision.remove
    assert "stash" in decision.reason


def test_clone_with_unmerged_second_branch_is_kept(repo):
    git(repo, "switch", "-qc", "side")
    commit(repo, "side-work")
    git(repo, "switch", "-q", "main")
    decision = check(repo)
    assert not decision.remove
    assert "side" in decision.reason


def test_folder_without_git_is_kept(tmp_path):
    folder = tmp_path / "copy"
    folder.mkdir()
    (folder / ".git").write_text("gitdir: /nowhere/.git/worktrees/copy\n")
    decision = check(folder)
    assert not decision.remove
    assert "not a git repository" in decision.reason


def test_discover_finds_worktrees_one_and_two_levels_down(repo, tmp_path):
    root = tmp_path / "wts"
    direct = add_worktree(repo, root, "direct")
    nested = add_worktree(repo, root / "wrapper", "nested")
    (root / "empty").mkdir()
    found = discover((str(root),))
    assert set(found) == {direct, nested}


def test_discover_expands_glob_roots(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "repos" / "one" / ".claude" / "worktrees", "x")
    assert discover((str(tmp_path / "repos" / "*" / ".claude" / "worktrees"),)) == [wt]


def test_remove_worktree_unregisters_it(repo, tmp_path):
    wt = add_worktree(repo, tmp_path / "wts", "gone")
    decision = check(wt)
    assert remove(decision) is None
    assert not wt.exists()
    assert str(wt) not in git(repo, "worktree", "list")


def test_remove_clone_deletes_folder(repo):
    assert remove(check(repo)) is None
    assert not repo.exists()


def test_remove_refuses_a_kept_decision(tmp_path):
    decision = Decision(path=tmp_path, remove=False, reason="kept", kind="clone")
    with pytest.raises(ValueError):
        remove(decision)


def test_remove_leftovers_deletes_only_empty_wrappers(tmp_path):
    root = tmp_path / "wts"
    (root / "empty").mkdir(parents=True)
    (root / "named").mkdir()
    (root / "named" / ".codex-worktree-name").write_text("named")
    (root / "kept").mkdir()
    (root / "kept" / "file.txt").write_text("data")
    removed = remove_leftovers((str(root),), apply=True)
    assert sorted(p.name for p in removed) == ["empty", "named"]
    assert (root / "kept").exists()
    assert not (root / "empty").exists()


def test_remove_leftovers_dry_run_keeps_folders(tmp_path):
    (tmp_path / "wts" / "empty").mkdir(parents=True)
    assert len(remove_leftovers((str(tmp_path / "wts"),), apply=False)) == 1
    assert (tmp_path / "wts" / "empty").exists()


def test_processes_cwds_parses_lsof_output(monkeypatch):
    output = "p1\nfcwd\nn/Users/me/a\np2\nfcwd\nn/Users/me/b\n"

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=output, stderr="")

    monkeypatch.setattr(worktrees.subprocess, "run", fake_run)
    assert worktrees.process_cwds() == ("/Users/me/a", "/Users/me/b")
