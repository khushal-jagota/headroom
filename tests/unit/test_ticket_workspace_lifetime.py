"""The system clock must not lose live files or unpublished repository work."""

from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.ticket_workspace_git import _git, _layout
from tests.unit.test_executor_native_runtime import ScriptedChild
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.worker_scratch import WorkerScratch
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacRunner
from planner.executor.protocol import ChildCommand, StartRun
from planner.ticket_workspaces.contracts import RETENTION_SECONDS, WorkspaceState
from planner.ticket_workspaces.data import read_workspace_states
from planner.ticket_workspaces.filesystem import (
    cleanup_local_workspaces,
    prepare_workspace,
    workspace_identity,
)
from planner.tickets.data import create_ticket


def test_end_clock_survives_edits_reopen_and_deletion_without_expiring_live_files(
    tmp_db: Connection,
    tmp_path: Path,
) -> None:
    ticket = create_ticket(
        tmp_db,
        title="Workspace clock",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )
    assert read_workspace_states(tmp_db)[ticket.id].ended_at is None
    tmp_db.execute("UPDATE tickets SET stage='done',updated_at=100 WHERE id=?", (ticket.id,))
    tmp_db.execute("UPDATE tickets SET recap='later edit',updated_at=200 WHERE id=?", (ticket.id,))
    assert read_workspace_states(tmp_db)[ticket.id].ended_at == 100
    tmp_db.execute(
        "UPDATE tickets SET stage='needs_implementation',updated_at=300 WHERE id=?", (ticket.id,)
    )
    assert read_workspace_states(tmp_db)[ticket.id].ended_at is None
    root = tmp_path / "files" / "tickets"
    folder = root / ticket.id
    prepare_workspace(folder, None, ticket.id)
    (folder / "artifacts" / "document.md").write_text("document")
    unknown = root / "t_aaaaaaaa"
    unknown.mkdir()
    now = RETENTION_SECONDS + 400
    cleanup_local_workspaces(root, read_workspace_states(tmp_db), now)
    assert folder.exists() and unknown.exists()
    tmp_db.execute("UPDATE tickets SET stage='done',updated_at=400 WHERE id=?", (ticket.id,))
    tmp_db.execute("DELETE FROM tickets WHERE id=?", (ticket.id,))
    state = read_workspace_states(tmp_db)[ticket.id]
    assert state == WorkspaceState(ticket.id, 400, True)
    cleanup_local_workspaces(root, read_workspace_states(tmp_db), now - 1)
    assert folder.exists()
    cleanup_local_workspaces(root, read_workspace_states(tmp_db), now)
    assert not folder.exists() and unknown.exists()
    assert (root.parent / "ticket-workspaces-cleanup.json").exists()


def test_cleanup_retains_unpublished_dirty_and_unverifiable_work_then_removes_safe_worktree(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    root = tmp_path / "files" / "tickets"
    folder = root / "t_aaaaaaaa"
    workspace = prepare_workspace(folder, layout.repository, "t_aaaaaaaa")
    assert prepare_workspace(folder, layout.repository, "t_aaaaaaaa") == workspace
    # A crashed backend leaves scratch outside Git, so it must not block retirement.
    scratch = WorkerScratch({"PLAN_TICKET_FILES_DIR": str(folder)})
    (scratch.directory / "leftover").write_text("disposable")
    _git(workspace, "commit", "-q", "--allow-empty", "-m", "unpublished")
    states = {"t_aaaaaaaa": WorkspaceState("t_aaaaaaaa", 0, False)}
    result = cleanup_local_workspaces(root, states, RETENTION_SECONDS)
    assert folder.exists() and "unpushed" in result.skipped[0]["reason"]
    _git(workspace, "push", "-q", "origin", "HEAD")
    dirty = workspace / "unfinished.txt"
    dirty.write_text("keep this work")
    result = cleanup_local_workspaces(root, states, RETENTION_SECONDS)
    assert folder.exists() and "uncommitted" in result.skipped[0]["reason"]
    dirty.unlink()
    remote = _git(workspace, "remote", "get-url", "origin").strip()
    _git(workspace, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    result = cleanup_local_workspaces(root, states, RETENTION_SECONDS)
    assert folder.exists() and "remote verification failed" in result.skipped[0]["reason"]
    _git(workspace, "remote", "set-url", "origin", remote)
    branch = _git(workspace, "branch", "--show-current").strip()
    _git(workspace, "push", "-q", "origin", "--delete", branch)
    _git(workspace, "update-ref", "refs/remotes/stale/illusion", "HEAD")
    _git(workspace, "config", "remote.origin.skipFetchAll", "true")
    result = cleanup_local_workspaces(root, states, RETENTION_SECONDS)
    assert folder.exists() and "unpushed" in result.skipped[0]["reason"]
    _git(workspace, "push", "-q", "origin", "HEAD")
    # Submodules require explicit owner review; a clean parent is not enough.
    _git(workspace, "-c", "protocol.file.allow=always", "submodule", "add", "-q", remote, "vendor")
    _git(workspace, "commit", "-q", "-m", "tracked submodule")
    _git(workspace, "push", "-q", "origin", "HEAD")
    result = cleanup_local_workspaces(root, states, RETENTION_SECONDS)
    assert folder.exists() and "submodules" in result.skipped[0]["reason"]
    _git(workspace, "submodule", "deinit", "-q", "-f", "--all")
    _git(workspace, "rm", "-q", "-f", ".gitmodules", "vendor")
    _git(workspace, "commit", "-q", "-m", "remove test submodule")
    _git(workspace, "push", "-q", "origin", "HEAD")
    result = cleanup_local_workspaces(root, states, RETENTION_SECONDS)
    assert result.removed == [str(folder)] and not folder.exists()
    assert str(workspace) not in _git(layout.repository, "worktree", "list")


def test_retirement_detaches_exact_workspace_before_reopen(
    tmp_db: Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.files.lifecycle import database_path
    from planner.files.logic.paths import ticket_files_root
    from planner.ticket_workspaces import filesystem
    from planner.ticket_workspaces.cleanup import clean_for_database

    ticket = create_ticket(
        tmp_db,
        title="Retirement race",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )
    root = ticket_files_root(database_path(tmp_db))
    folder = root / ticket.id
    prepare_workspace(folder, None, ticket.id)
    import asyncio
    import sqlite3

    from tests.support.in_memory_conversation_system import InMemoryConversationSystem

    from planner.runtime.conversation_start import start_ticket_conversation, worker_resolve
    from planner.ticket_workspaces.data import authorize_retirement

    system = InMemoryConversationSystem()
    conversation_id = "cv_workspace_retirement"
    asyncio.run(
        start_ticket_conversation(
            system,
            tmp_db,
            ticket,
            worker_resolve(tmp_db, ticket),
            conversation_id=conversation_id,
            now=2,
        )
    )
    tmp_db.execute("UPDATE tickets SET stage='done',updated_at=100 WHERE id=?", (ticket.id,))
    tmp_db.execute(
        "INSERT INTO conversation_lifecycle_facts "
        "(conversation_id,kind,identity,turn_number,operation,status) "
        "VALUES(?,'turn','first',1,'turn','running')",
        (conversation_id,),
    )
    state = read_workspace_states(tmp_db)[ticket.id]
    assert not authorize_retirement(
        tmp_db, state, RETENTION_SECONDS + 100, workspace_identity(folder)
    )
    tmp_db.execute(
        "UPDATE conversation_lifecycle_facts SET status='completed' WHERE conversation_id=?",
        (conversation_id,),
    )
    tmp_db.commit()
    remove = filesystem._remove_retired

    def reopen_after_detach(retired: Path) -> str | None:
        assert retired != folder and not folder.exists()
        # Atomic rename must release the writer before slow deletion starts.
        tmp_db.execute(
            "UPDATE tickets SET stage='needs_implementation',updated_at=200 WHERE id=?",
            (ticket.id,),
        )
        tmp_db.commit()
        previous_identity = workspace_identity(retired)
        prepare_workspace(folder, None, ticket.id)
        assert workspace_identity(folder) != previous_identity
        (folder / "artifacts" / "new.md").write_text("live workspace")
        return remove(retired)

    monkeypatch.setattr(filesystem, "_remove_retired", reopen_after_detach)
    refreshed_end = read_workspace_states(tmp_db)[ticket.id].ended_at
    assert refreshed_end is not None
    clean_for_database(database_path(tmp_db), refreshed_end + RETENTION_SECONDS)
    assert (folder / "artifacts" / "new.md").read_text() == "live workspace"
    assert read_workspace_states(tmp_db)[ticket.id].ended_at is None
    # A stale native conversation cannot create provider work after its grant.
    with pytest.raises(sqlite3.IntegrityError, match="workspace is retired"):
        tmp_db.execute(
            "INSERT INTO conversation_lifecycle_facts "
            "(conversation_id,kind,identity,turn_number,operation,status) "
            "VALUES(?,'turn','stale',2,'turn','intent')",
            (conversation_id,),
        )
    from tests.unit.test_conversation_start_wiring import _sent

    asyncio.run(_sent(system, tmp_db, ticket.id, "Work in the new workspace", now=300))
    replacement = tmp_db.execute(
        "SELECT conversation_id FROM tickets WHERE id=?", (ticket.id,)
    ).fetchone()[0]
    assert replacement != conversation_id


def test_new_activity_in_done_ticket_restarts_the_full_file_clock(tmp_db: Connection) -> None:
    """Regression 1: both recreated and retained Done workspaces need a full new wait."""
    import asyncio

    from tests.support.in_memory_conversation_system import InMemoryConversationSystem
    from tests.unit.test_conversation_start_wiring import _sent

    from planner.files.lifecycle import database_path
    from planner.files.logic.paths import ticket_files_root
    from planner.ticket_workspaces.cleanup import clean_for_database

    ticket = create_ticket(
        tmp_db,
        title="Done activity",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )
    folder = ticket_files_root(database_path(tmp_db)) / ticket.id
    prepare_workspace(folder, None, ticket.id)
    tmp_db.execute("UPDATE tickets SET stage='done',updated_at=100 WHERE id=?", (ticket.id,))
    tmp_db.commit()
    clean_for_database(database_path(tmp_db), RETENTION_SECONDS + 100)
    assert not folder.exists()
    system = InMemoryConversationSystem()
    now = 2 * RETENTION_SECONDS
    asyncio.run(_sent(system, tmp_db, ticket.id, "Produce new files", now=now))
    conversation = tmp_db.execute(
        "SELECT conversation_id FROM tickets WHERE id=?", (ticket.id,)
    ).fetchone()[0]
    system.complete_running_turn(conversation)
    (folder / "artifacts" / "fresh.md").write_text("new result")
    clean_for_database(database_path(tmp_db), now + RETENTION_SECONDS - 1)
    assert (folder / "artifacts" / "fresh.md").exists()
    # Further work in that same folder renews the clock too.
    asyncio.run(
        _sent(system, tmp_db, ticket.id, "Continue the result", now=now + RETENTION_SECONDS - 1)
    )
    system.complete_running_turn(conversation)
    clean_for_database(database_path(tmp_db), now + RETENTION_SECONDS)
    assert (folder / "artifacts" / "fresh.md").exists()
    # Background output can arrive long after the foreground turn ends.
    import time

    tmp_db.execute(
        "INSERT INTO conversation_background_tasks VALUES(?,?,?)",
        (conversation, "long-task", "writes a late result"),
    )
    tmp_db.execute(
        "UPDATE ticket_workspace_lifecycle SET ended_at=100 WHERE ticket_id=?", (ticket.id,)
    )
    tmp_db.commit()
    finished_at = int(time.time())
    clean_for_database(database_path(tmp_db), finished_at)
    assert folder.exists()
    (folder / "artifacts" / "background.md").write_text("late output")
    tmp_db.execute(
        "DELETE FROM conversation_background_tasks WHERE conversation_id=?", (conversation,)
    )
    tmp_db.commit()
    renewed = read_workspace_states(tmp_db)[ticket.id].ended_at
    assert renewed is not None and renewed >= finished_at
    clean_for_database(database_path(tmp_db), renewed + RETENTION_SECONDS - 1)
    assert (folder / "artifacts" / "background.md").read_text() == "late output"


def test_home_mirror_retirement_does_not_displace_unpushed_mac_workspace(
    tmp_db: Connection,
    tmp_path: Path,
    native_registry: tuple[MacRunner, MacOutbox, ScriptedChild, ChildCommand],
) -> None:
    """Regression 2: real server roles must resume the Mac's existing unsafe checkout."""
    import subprocess
    from dataclasses import replace

    from planner.execution_identity import TicketLocale
    from planner.files.lifecycle import database_path
    from planner.files.logic.paths import ticket_files_root
    from planner.projects.data import create_project
    from planner.runtime.conversation_start import worker_resolve
    from planner.ticket_workspaces.cleanup import clean_for_database
    from planner.ticket_workspaces.data import ensure_workspace_activity, has_committed_retirement

    layout = _layout(tmp_path)
    registry, _, _, native_start = native_registry
    start_args = native_start.arguments
    assert isinstance(start_args, StartRun)
    project = create_project(tmp_db, name="Mac Project", folder_path=layout.repository, now=1)
    ticket = create_ticket(
        tmp_db,
        title="Mac work",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        locale=TicketLocale.macos,
        project_id=project.id,
    )
    mac_source = registry.paths.workspace / "projects" / project.id
    mac_source.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(layout.repository), str(mac_source)], check=True)
    _git(mac_source, "config", "user.email", "test@example.com")
    _git(mac_source, "config", "user.name", "Test")

    def localize_current_server_role() -> ResolvedConversationStart:
        values = worker_resolve(tmp_db, ticket)
        resolved = replace(
            start_args.resolved_start,
            role_materials=values.role_materials,
            workspace_folder=values.workspace_folder,
            workspace_path_policy=values.workspace_path_policy,
        )
        return registry._localize_start(
            resolved,
            native_start.child,
            "capability",
            str(values.workspace_folder).lstrip("/"),
            dict(values.role_materials.identity_environment_variables),
            tmp_path / "skills",
        )

    initial_start = localize_current_server_role()
    mac_root = registry.paths.artifacts / ticket.id
    mac_worktree = initial_start.workspace_folder
    mac_identity = workspace_identity(mac_root)
    _git(mac_worktree, "commit", "-q", "--allow-empty", "-m", "Mac unpublished work")
    (mac_worktree / "uncommitted.txt").write_text("Mac draft")
    home_root = ticket_files_root(database_path(tmp_db)) / ticket.id
    home_root.mkdir(parents=True)
    (home_root / "review.md").write_text("mirrored artifact")
    home_identity = workspace_identity(home_root)
    tmp_db.execute("UPDATE tickets SET stage='done',updated_at=100 WHERE id=?", (ticket.id,))
    tmp_db.commit()
    state = read_workspace_states(tmp_db)[ticket.id]
    clean_for_database(database_path(tmp_db), RETENTION_SECONDS + 100)
    assert not home_root.exists() and has_committed_retirement(tmp_db, state, home_identity)
    assert not has_committed_retirement(tmp_db, state, mac_identity)
    tmp_db.execute(
        "UPDATE tickets SET stage='needs_implementation',updated_at=? WHERE id=?",
        (RETENTION_SECONDS + 200, ticket.id),
    )
    tmp_db.commit()
    ensure_workspace_activity(tmp_db, ticket.id, RETENTION_SECONDS + 200)
    resumed_start = localize_current_server_role()
    assert resumed_start.workspace_folder == mac_worktree
    assert workspace_identity(mac_root) == mac_identity
    assert (resumed_start.workspace_folder / "uncommitted.txt").read_text() == "Mac draft"
    assert (
        _git(resumed_start.workspace_folder, "log", "-1", "--format=%s").strip()
        == "Mac unpublished work"
    )


def test_legacy_scratch_outside_ticket_files_stays_for_owner_review(
    tmp_path: Path,
) -> None:
    """Regression 4: cleanup never relocates an old developer workspace into live data."""
    layout = _layout(tmp_path)
    legacy = layout.folders[2] / "t_aaaaaaaa"
    _git(layout.repository, "worktree", "add", "-q", "-b", "legacy-ticket", str(legacy), "HEAD")
    before = _git(layout.repository, "worktree", "list", "--porcelain")
    root = tmp_path / "deployment" / "data" / "files" / "tickets"
    state = {"t_aaaaaaaa": WorkspaceState("t_aaaaaaaa", 0, False)}
    result = cleanup_local_workspaces(
        root, state, RETENTION_SECONDS, extra_roots=(layout.folders[2],)
    )
    assert legacy.exists() and _git(layout.repository, "worktree", "list", "--porcelain") == before
    assert not (root.parent / ".ticket-retired").exists()
    assert "one-time owner review" in result.skipped[0]["reason"]
