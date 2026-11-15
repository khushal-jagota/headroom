"""Interrupted system-owned preparation must resume without losing repository work."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tests.support.ticket_workspace_git import _git, _layout

from planner.ticket_workspaces import filesystem
from planner.ticket_workspaces.contracts import WorkspaceState
from planner.ticket_workspaces.filesystem import prepare_workspace as prepare_current_workspace
from planner.ticket_workspaces.filesystem import retire_workspace


def prepare_workspace(folder: Path, repository: Path | None, ticket_id: str) -> Path:
    """Exercise legacy linked-worktree recovery with its existing ownership marker."""
    if repository is not None and not (folder / ".panels-workspace.json").exists():
        identity = filesystem.workspace_identity(folder)
        marker_path = folder / ".panels-workspace.json"
        marker = json.loads(marker_path.read_text())
        marker.update(
            repository=str(repository.resolve()),
            branch=f"ticket/{ticket_id}-workspace-{identity.workspace_id[:12]}",
            worktree=str(folder.resolve() / "worktree"),
            preparing=True,
            initial_commit=_git(repository, "rev-parse", "HEAD").strip(),
        )
        filesystem._write_json(marker_path, marker)
    return prepare_current_workspace(folder, repository, ticket_id)


def test_replacement_repairs_retired_worktree_registration_before_reusing_its_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(filesystem, "process_in_use", lambda _path: None)
    layout = _layout(tmp_path)
    folder = tmp_path / "files/tickets/t_aaaaaaaa"
    old = prepare_workspace(folder, layout.repository, "t_aaaaaaaa")
    retired = retire_workspace(folder, WorkspaceState("t_aaaaaaaa", 0, False))
    assert isinstance(retired, Path)
    replacement = prepare_workspace(folder, layout.repository, "t_aaaaaaaa")
    assert replacement == old
    assert _git(replacement, "status", "--porcelain") == ""
    listing = _git(layout.repository, "worktree", "list", "--porcelain")
    assert f"worktree {retired / 'worktree'}\n" in listing
    assert f"worktree {replacement}\n" in listing
    assert (retired / "worktree/.git").is_file()
    # Cleanup can remove Git's checkout, then stop before it removes the outer folder.
    _git(layout.repository, "worktree", "remove", "--force", str(retired / "worktree"))
    assert retired.exists() and not (retired / "worktree").exists()
    second_retired = retire_workspace(folder, WorkspaceState("t_aaaaaaaa", 0, False))
    assert isinstance(second_retired, Path)
    assert prepare_workspace(folder, layout.repository, folder.name) == replacement
    assert _git(replacement, "status", "--porcelain") == ""


def test_preparation_recovers_only_its_recorded_interrupted_git_or_directory_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _layout(tmp_path)
    tickets = tmp_path / "files/tickets"
    run_git = filesystem._git
    folder = tickets / "t_aaaaaaaa"

    def timed_out_after_add(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
        result = run_git(repository, *args)
        if args[:2] == ("worktree", "add"):
            assert result.returncode == 0
            raise subprocess.TimeoutExpired(args, 60)
        return result

    with monkeypatch.context() as failure:
        failure.setattr(filesystem, "_git", timed_out_after_add)
        with pytest.raises(subprocess.TimeoutExpired):
            prepare_workspace(folder, layout.repository, folder.name)
    interrupted = json.loads((folder / ".panels-workspace.json").read_text())
    assert interrupted["preparing"] is True
    owned_checkout = folder / "worktree"
    _git(owned_checkout, "commit", "-q", "--allow-empty", "-m", "work after interruption")
    committed = _git(owned_checkout, "rev-parse", "HEAD")
    unfinished = owned_checkout / "unfinished.txt"
    unfinished.write_text("local work after interruption")
    workspace = prepare_workspace(folder, layout.repository, folder.name)
    assert _git(workspace, "branch", "--show-current").strip() == interrupted["branch"]
    assert _git(workspace, "rev-parse", "HEAD") == committed
    assert unfinished.read_text() == "local work after interruption"

    # Git can create the branch before it creates or registers the checkout.
    leftover = tickets / "t_bbbbbbbb"

    def timed_out_after_branch(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:3] == ("worktree", "add", "-b"):
            created = run_git(repository, "branch", args[3], args[5])
            assert created.returncode == 0
            raise subprocess.TimeoutExpired(args, 60)
        return run_git(repository, *args)

    with monkeypatch.context() as failure:
        failure.setattr(filesystem, "_git", timed_out_after_branch)
        with pytest.raises(subprocess.TimeoutExpired):
            prepare_workspace(leftover, layout.repository, leftover.name)
    assert not (leftover / "worktree").exists()
    workspace = prepare_workspace(leftover, layout.repository, leftover.name)
    assert _git(workspace, "status", "--porcelain") == ""

    # A plain project's mkdir can succeed before the ready marker write fails.
    plain = tickets / "t_cccccccc"
    write_json = filesystem._write_json

    def fail_ready_marker(path: Path, value: Any) -> None:
        if path.name == ".panels-workspace.json" and value.get("preparing") is False:
            raise OSError("interrupted final ownership write")
        write_json(path, value)

    with monkeypatch.context() as failure:
        failure.setattr(filesystem, "_write_json", fail_ready_marker)
        with pytest.raises(OSError, match="final ownership"):
            prepare_workspace(plain, None, plain.name)
    assert (plain / "worktree").is_dir()
    assert prepare_workspace(plain, None, plain.name) == plain / "worktree"

    unowned = tickets / "t_dddddddd"
    (unowned / "worktree").mkdir(parents=True)
    foreign = unowned / "worktree/unfinished.txt"
    foreign.write_text("unowned work")
    with pytest.raises(RuntimeError, match="without preparation ownership"):
        prepare_workspace(unowned, None, unowned.name)
    assert foreign.read_text() == "unowned work"


def test_interrupted_partial_checkout_is_not_adopted_until_index_and_files_are_complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layout = _layout(tmp_path)
    (layout.repository / "present.txt").write_text("original present file")
    (layout.repository / "missing.txt").write_text("required tracked file")
    _git(layout.repository, "add", "present.txt", "missing.txt")
    _git(layout.repository, "commit", "-q", "-m", "tracked checkout content")
    folder = tmp_path / "files/tickets/t_aaaaaaaa"
    run_git = filesystem._git

    def interrupted_checkout(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
        if args[:3] == ("worktree", "add", "-b"):
            result = run_git(repository, "worktree", "add", "--no-checkout", *args[2:])
            assert result.returncode == 0
            checkout = Path(args[4])
            _git(checkout, "checkout", "HEAD", "--", "present.txt")
            raise subprocess.TimeoutExpired(args, 60)
        return run_git(repository, *args)

    with monkeypatch.context() as failure:
        failure.setattr(filesystem, "_git", interrupted_checkout)
        with pytest.raises(subprocess.TimeoutExpired):
            prepare_workspace(folder, layout.repository, folder.name)
    checkout = folder / "worktree"
    # Registration and .git exist, but Git never populated the complete index or tree.
    assert (checkout / ".git").is_file()
    assert _git(checkout, "ls-files").splitlines() == ["present.txt"]
    (checkout / "present.txt").write_text("preserve unfinished local work")
    with pytest.raises(RuntimeError, match="incomplete"):
        prepare_workspace(folder, layout.repository, folder.name)
    assert json.loads((folder / ".panels-workspace.json").read_text())["preparing"] is True
    # A complete index alone still does not prove that Git populated all tracked files.
    _git(checkout, "read-tree", "HEAD")
    with pytest.raises(RuntimeError, match="incomplete"):
        prepare_workspace(folder, layout.repository, folder.name)
    _git(checkout, "checkout-index", "--", "missing.txt")
    # A killed Git add also retains its initialization lock. All paths can exist
    # while one file is truncated, so path and index checks alone cannot admit it.
    metadata = Path(_git(checkout, "rev-parse", "--absolute-git-dir").strip())
    initialization_lock = metadata / "locked"
    initialization_lock.write_text("initializing")
    (checkout / "missing.txt").write_text("required")
    assert _git(checkout, "ls-files").splitlines() == ["missing.txt", "present.txt"]
    assert (checkout / "present.txt").is_file() and (checkout / "missing.txt").is_file()
    with pytest.raises(RuntimeError, match="initialization lock"):
        prepare_workspace(folder, layout.repository, folder.name)
    assert (checkout / "missing.txt").read_text() == "required"
    assert initialization_lock.exists()
    assert json.loads((folder / ".panels-workspace.json").read_text())["preparing"] is True
    (checkout / "missing.txt").write_text("required tracked file")
    initialization_lock.unlink()
    assert prepare_workspace(folder, layout.repository, folder.name) == checkout
    assert (checkout / "present.txt").read_text() == "preserve unfinished local work"
    assert (checkout / "missing.txt").read_text() == "required tracked file"


def test_artifact_only_retirement_does_not_require_git_repair_before_new_checkout(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    folder = tmp_path / "files/tickets/t_aaaaaaaa"
    filesystem.workspace_identity(folder)
    (folder / "report.md").write_text("retired artifact")
    retired = retire_workspace(folder, WorkspaceState(folder.name, 0, False))
    assert isinstance(retired, Path)
    assert "worktree" not in json.loads((retired / ".panels-workspace.json").read_text())
    checkout = prepare_workspace(folder, layout.repository, folder.name)
    assert _git(checkout, "status", "--porcelain") == ""
    assert (retired / "report.md").read_text() == "retired artifact"


def test_repository_selection_refuses_malformed_or_ambiguous_children(tmp_path: Path) -> None:
    """A unique child must be Git-valid, and multiple candidates must not be guessed."""
    project = tmp_path / "project"
    malformed = project / "headroom"
    (malformed / ".git").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="Cannot inspect Project repository"):
        filesystem.repository_root(project)
    other = project / "other"
    other.mkdir()
    _git(other, "init", "-q")
    with pytest.raises(RuntimeError, match="select the repository folder explicitly"):
        filesystem.repository_root(project)


def test_new_clone_uses_fresh_remote_without_shared_git_storage(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    repository = layout.repository
    old = _git(repository, "rev-parse", "HEAD").strip()
    _git(repository, "commit", "-q", "--allow-empty", "-m", "new remote")
    new = _git(repository, "rev-parse", "HEAD").strip()
    _git(repository, "push", "-q", "origin", "HEAD:staging")
    _git(repository, "reset", "-q", "--hard", old)
    (repository / "local.txt").write_text("unfinished")
    folder = tmp_path / "files/tickets/t_aaaaaaaa"
    checkout = prepare_current_workspace(folder, repository, folder.name)
    assert _git(checkout, "rev-parse", "HEAD").strip() == new
    assert (checkout / ".git").is_dir()
    assert not (checkout / ".git/objects/info/alternates").exists()
    assert _git(repository, "rev-parse", "HEAD").strip() == old
    assert (repository / "local.txt").read_text() == "unfinished"
    _git(repository, "remote", "set-url", "origin", str(tmp_path / "missing"))
    assert prepare_current_workspace(folder, repository, folder.name) == checkout
    another = tmp_path / "files/tickets/t_bbbbbbbb"
    with pytest.raises(RuntimeError, match="Cannot clone"):
        prepare_current_workspace(another, repository, another.name)
    assert not (another / "worktree").exists()


def test_panels_clone_uses_staging_without_project_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = _layout(tmp_path)
    origin = _git(layout.repository, "remote", "get-url", "origin").strip()
    _git(layout.repository, "push", "-q", "origin", "HEAD:main")
    _git(Path(origin), "symbolic-ref", "HEAD", "refs/heads/main")
    _git(layout.repository, "commit", "-q", "--allow-empty", "-m", "staging")
    wanted = _git(layout.repository, "rev-parse", "HEAD").strip()
    _git(layout.repository, "push", "-q", "origin", "HEAD:staging")
    monkeypatch.setattr(filesystem, "_PANELS_ORIGIN", origin)
    folder = tmp_path / "files/tickets/t_aaaaaaaa"
    ambiguous = tmp_path / "ambiguous"
    for name in ("one", "two"):
        (ambiguous / name / ".git").mkdir(parents=True)
    checkout = prepare_current_workspace(folder, ambiguous, folder.name, panels_project=True)
    resumed = prepare_current_workspace(folder, ambiguous, folder.name, panels_project=True)
    assert resumed == checkout
    assert _git(checkout, "rev-parse", "HEAD").strip() == wanted


def test_cleanup_of_single_branch_clone_with_unfetched_remote_branch(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    origin = _git(layout.repository, "remote", "get-url", "origin").strip()
    folder = tmp_path / "files/tickets/t_aaaaaaaa"
    checkout = prepare_current_workspace(folder, layout.repository, folder.name)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", origin, str(other)], check=True)
    _git(other, "checkout", "-q", "--orphan", "unrelated")
    _git(
        other,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "unrelated",
    )
    _git(other, "push", "-q", "origin", "HEAD:unrelated")
    # The checkout's ordinary fetch is limited to staging. The unrelated advertised
    # object must not make a remotely preserved checkout impossible to retire.
    assert filesystem.reason_to_keep(checkout) is None


def test_cleanup_ignores_ancestor_ticket_names_but_detects_nested_owners(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    root = tmp_path / "t_bbbbbbbb-scratch" / "t_aaaaaaaa"
    checkout = prepare_current_workspace(root, layout.repository, root.name)
    assert filesystem.workspace_ticket_owners(root) == ("t_aaaaaaaa",)
    nested = checkout / "t_cccccccc"
    _git(checkout, "init", "-q", str(nested))
    assert set(filesystem.workspace_ticket_owners(root)) == {"t_aaaaaaaa", "t_cccccccc"}
