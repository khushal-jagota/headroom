"""Server-side cleanup uses the same clock as the Mac executor."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from planner.core.db import connect
from planner.files.logic.paths import ticket_files_root
from planner.ticket_workspaces.contracts import (
    CleanupResult,
    WorkspaceIdentity,
    WorkspaceRetirementOperation,
    WorkspaceState,
)
from planner.ticket_workspaces.data import (
    authorize_retirement,
    has_committed_retirement,
    read_workspace_states,
)
from planner.ticket_workspaces.filesystem import (
    cleanup_local_workspaces,
    retire_workspace,
)


def clean_for_database(
    db_path: str,
    now: int,
    *,
    extra_roots: tuple[Path, ...] = (),
    apply: bool = True,
    retire_local_children: WorkspaceRetirementOperation | None = None,
) -> CleanupResult:
    with closing(connect(db_path)) as conn:
        states = read_workspace_states(conn)
        active = frozenset(
            str(row[0])
            for row in conn.execute("SELECT id FROM tickets WHERE worker_step_claim='out'")
        )

    def custody_retired(conn: sqlite3.Connection, identity: WorkspaceIdentity) -> bool:
        # A separate CLI process cannot stop or release a live runtime's children.
        # Missing legacy custody keeps its existing policy. Any recorded uncertainty
        # stays fenced until the runtime positively retires it.
        return conn.execute(
            "SELECT 1 FROM ticket_workspace_conversations w "
            "JOIN conversation_child_custody c USING(conversation_id) "
            "WHERE w.machine_id=? AND w.workspace_id=? AND c.state!='retired' LIMIT 1",
            (identity.machine_id, identity.workspace_id),
        ).fetchone() is None

    def granted(expected: WorkspaceState, identity: WorkspaceIdentity) -> bool:
        def check() -> Path | str:
            with closing(connect(db_path)) as fresh:
                return (
                    Path(".")
                    if custody_retired(fresh, identity)
                    and has_committed_retirement(fresh, expected, identity)
                    else "Local child retirement is not proven"
                )
        result = (
            retire_local_children(expected.ticket_id, identity, check)
            if retire_local_children is not None else check()
        )
        return isinstance(result, Path)

    def detach(path: Path, expected: WorkspaceState, identity: WorkspaceIdentity) -> Path | str:
        with closing(connect(db_path)) as fresh:
            fresh.execute("BEGIN IMMEDIATE")
            target: Path | str | None = None
            try:
                if not custody_retired(fresh, identity):
                    fresh.rollback()
                    return "Local child retirement is not proven"
                current = read_workspace_states(fresh).get(expected.ticket_id)
                if current != expected or not authorize_retirement(fresh, expected, now, identity):
                    fresh.rollback()
                    return "Ticket is active or no longer eligible"
                target = retire_workspace(
                    path,
                    expected,
                    identity=identity,
                    retired_root=ticket_files_root(db_path).parent / ".ticket-retired",
                )
                fresh.commit()
                return target
            except BaseException:
                if isinstance(target, Path) and target.exists() and not path.exists():
                    target.rename(path)
                fresh.rollback()
                raise

    def retire(path: Path, expected: WorkspaceState, identity: WorkspaceIdentity) -> Path | str:
        def operation() -> Path | str:
            return detach(path, expected, identity)
        return (
            retire_local_children(expected.ticket_id, identity, operation)
            if retire_local_children is not None else operation()
        )

    result = cleanup_local_workspaces(
        ticket_files_root(db_path),
        states,
        now,
        extra_roots=extra_roots,
        active_ticket_ids=active,
        apply=apply,
        retire=retire,
        retirement_is_committed=granted,
    )
    return result
