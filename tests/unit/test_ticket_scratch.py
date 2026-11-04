"""Ticket scratch cleanup removes finished Tickets' scratch and never removes work."""

from __future__ import annotations

import subprocess
from pathlib import Path

from planner.environments import ticket_scratch

NOW = 1_000_000
DONE = ("done", NOW - ticket_scratch.SETTLE_SECONDS)
LIVE = ("needs_implementation", NOW)


def _git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True, text=True
    ).stdout


def _layout(tmp_path: Path) -> ticket_scratch.ScratchLayout:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "staging", str(origin)], check=True)
    root = tmp_path / "panels"
    repository = root / "repository"
    subprocess.run(["git", "clone", "-q", str(origin), str(repository)], check=True)
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "commit", "-q", "--allow-empty", "-m", "base")
    _git(repository, "push", "-q", "origin", "HEAD:staging")
    _git(repository, "fetch", "-q", "origin")
    layout = ticket_scratch.ScratchLayout.under(root, home=tmp_path / "home")
    for folder in layout.folders:
        folder.mkdir(parents=True)
    return layout


def _worktree(layout: ticket_scratch.ScratchLayout, path: Path, branch: str) -> Path:
    _git(layout.repository, "worktree", "add", "-q", "-b", branch, str(path), "origin/staging")
    return path


def _clean(
    layout: ticket_scratch.ScratchLayout, states: dict[str, tuple[str, int]]
) -> ticket_scratch.ScratchResult:
    scratches = ticket_scratch.collect_ticket_scratch(layout, states, NOW)
    return ticket_scratch.remove_ticket_scratch(layout, scratches, states, NOW)


def test_done_ticket_scratch_and_merged_branch_are_removed(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    task_tmp, worktrees, repository_worktrees, pct, evidence = layout.folders
    worktree = _worktree(layout, repository_worktrees / "t_aaaaaaaa", "ticket/t_aaaaaaaa")
    (worktree / "node_modules").mkdir()
    (task_tmp / "t_aaaaaaaa-build").mkdir()
    (task_tmp / "t_aaaaaaaa-run.log").write_text("log")
    (pct / "t_aaaaaaaa").mkdir()
    (evidence / "t_aaaaaaaa").mkdir()
    (task_tmp / "t_bbbbbbbb").mkdir()
    (task_tmp / "notes.log").write_text("no Ticket")

    result = _clean(layout, {"t_aaaaaaaa": DONE, "t_bbbbbbbb": LIVE})

    assert not worktree.exists()
    assert not (task_tmp / "t_aaaaaaaa-build").exists()
    assert not (task_tmp / "t_aaaaaaaa-run.log").exists()
    assert not (pct / "t_aaaaaaaa").exists()
    assert not (evidence / "t_aaaaaaaa").exists()
    assert (task_tmp / "t_bbbbbbbb").is_dir()
    assert (task_tmp / "notes.log").is_file()
    assert result.deleted_branches == ["ticket/t_aaaaaaaa"]
    assert "t_aaaaaaaa" not in _git(layout.repository, "worktree", "list")
    assert result.skipped == []


def test_work_is_kept_and_reported(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    task_tmp, _, repository_worktrees, _, _ = layout.folders
    dirty = _worktree(layout, repository_worktrees / "t_cccccccc", "ticket/t_cccccccc")
    (dirty / "draft.py").write_text("print('unsaved')\n")
    unpushed = _worktree(layout, repository_worktrees / "t_dddddddd", "feature/goal-holders")
    _git(unpushed, "commit", "-q", "--allow-empty", "-m", "local only")
    # A deleted Ticket's folder holds the worktree of a live Ticket.
    _worktree(layout, task_tmp / "t_eeeeeeee" / "source", "ticket/t_ffffffff")

    result = _clean(layout, {"t_cccccccc": DONE, "t_dddddddd": DONE, "t_ffffffff": LIVE})

    assert dirty.is_dir() and unpushed.is_dir() and (task_tmp / "t_eeeeeeee").is_dir()
    reasons = {Path(item["path"]).name: item["reason"] for item in result.skipped}
    assert reasons["t_cccccccc"].startswith("uncommitted changes")
    assert reasons["t_dddddddd"].startswith("commits that no remote has")
    assert reasons["t_eeeeeeee"].startswith("holds the worktree")
    assert "feature/goal-holders" in _git(layout.repository, "branch")


def test_a_ticket_inside_its_settle_period_is_left_alone(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.folders[0] / "t_gggggggg").mkdir()

    result = _clean(layout, {"t_gggggggg": ("done", NOW - 1)})

    assert (layout.folders[0] / "t_gggggggg").is_dir()
    assert result.removed == []
