"""Server-side cleanup uses the same clock as the Mac executor."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from planner.core.db import connect
from planner.files.logic.paths import ticket_files_root
from planner.ticket_workspaces.contracts import CleanupResult, WorkspaceIdentity, WorkspaceState
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
) -> CleanupResult:
    with closing(connect(db_path)) as conn:
        states = read_workspace_states(conn)
        active = frozenset(
            str(row[0])
            for row in conn.execute("SELECT id FROM tickets WHERE worker_step_claim='out'")
        )

    def granted(expected: WorkspaceState, identity: WorkspaceIdentity) -> bool:
        with closing(connect(db_path)) as fresh:
            return has_committed_retirement(fresh, expected, identity)

    def retire(path: Path, expected: WorkspaceState, identity: WorkspaceIdentity) -> Path | str:
        with closing(connect(db_path)) as fresh:
            fresh.execute("BEGIN IMMEDIATE")
            target: Path | str | None = None
            try:
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
