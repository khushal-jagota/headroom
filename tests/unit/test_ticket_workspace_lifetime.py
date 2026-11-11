"""The system clock must not lose live files or unpublished repository work."""

from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.ticket_workspace_git import _git, _layout

from planner.ticket_workspaces.contracts import RETENTION_SECONDS, WorkspaceState
from planner.ticket_workspaces.data import read_workspace_states
from planner.ticket_workspaces.filesystem import cleanup_local_workspaces, prepare_workspace
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
    result = cleanup_local_workspaces(root, states, RETENTION_SECONDS)
    assert result.removed == [str(folder)] and not folder.exists()
    assert str(workspace) not in _git(layout.repository, "worktree", "list")


def test_retirement_detaches_old_generation_before_reopen(
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
    assert not authorize_retirement(tmp_db, state, RETENTION_SECONDS + 100)
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
        generation = read_workspace_states(tmp_db)[ticket.id].generation
        assert generation == 1
        prepare_workspace(folder, None, ticket.id, generation=generation)
        (folder / "artifacts" / "new.md").write_text("live generation")
        return remove(retired)

    monkeypatch.setattr(filesystem, "_remove_retired", reopen_after_detach)
    clean_for_database(database_path(tmp_db), RETENTION_SECONDS + 100)
    assert (folder / "artifacts" / "new.md").read_text() == "live generation"
    assert read_workspace_states(tmp_db)[ticket.id].ended_at is None
    # A stale native conversation cannot create provider work after its grant.
    with pytest.raises(sqlite3.IntegrityError, match="generation is retired"):
        tmp_db.execute(
            "INSERT INTO conversation_lifecycle_facts "
            "(conversation_id,kind,identity,turn_number,operation,status) "
            "VALUES(?,'turn','stale',2,'turn','intent')",
            (conversation_id,),
        )
    # Pre-feature native sessions have no generation field and belong to generation zero.
    import json

    raw_environment = tmp_db.execute(
        "SELECT identity_environment_variables FROM conversations WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()[0]
    legacy_environment = [
        pair
        for pair in json.loads(raw_environment)
        if pair[0] != "PLAN_TICKET_WORKSPACE_GENERATION"
    ]
    tmp_db.execute(
        "UPDATE conversations SET identity_environment_variables=? WHERE conversation_id=?",
        (json.dumps(legacy_environment), conversation_id),
    )
    with pytest.raises(sqlite3.IntegrityError, match="generation is retired"):
        tmp_db.execute(
            "INSERT INTO conversation_lifecycle_facts "
            "(conversation_id,kind,identity,turn_number,operation,status) "
            "VALUES(?,'turn','legacy-stale',2,'turn','intent')",
            (conversation_id,),
        )
    from tests.unit.test_conversation_start_wiring import _sent

    asyncio.run(_sent(system, tmp_db, ticket.id, "Work in the new generation", now=300))
    replacement = tmp_db.execute(
        "SELECT conversation_id FROM tickets WHERE id=?", (ticket.id,)
    ).fetchone()[0]
    assert replacement != conversation_id
