"""Server-side cleanup uses the same clock as the Mac executor."""

from __future__ import annotations

import json
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
    detach_workspace,
    prepare_workspace_retirement,
    read_workspace_identity,
)


def clean_for_database(
    db_path: str,
    now: int,
    *,
    extra_roots: tuple[Path, ...] = (),
    apply: bool = True,
    measure_size: bool = False,
    retire_local_children: WorkspaceRetirementOperation | None = None,
) -> CleanupResult:
    with closing(connect(db_path)) as conn:
        states = read_workspace_states(conn)
        active = frozenset(
            str(row[0])
            for row in conn.execute("SELECT id FROM tickets WHERE worker_step_claim='out'")
        )

    def custody_retired(
        conn: sqlite3.Connection, identity: WorkspaceIdentity, *, require_registration: bool
    ) -> bool:
        # A separate CLI process cannot stop or release a live runtime's children.
        # A registered conversation without a retired custody record is uncertain.
        if require_registration and conn.execute(
            "SELECT 1 FROM ticket_workspace_conversations "
            "WHERE machine_id=? AND workspace_id=? LIMIT 1",
            (identity.machine_id, identity.workspace_id),
        ).fetchone() is None:
            return False
        return (
            conn.execute(
                "SELECT 1 FROM ticket_workspace_conversations w "
                "LEFT JOIN conversation_child_custody c USING(conversation_id) "
                "WHERE w.machine_id=? AND w.workspace_id=? "
                "AND (c.state IS NULL OR c.state!='retired') LIMIT 1",
                (identity.machine_id, identity.workspace_id),
            ).fetchone()
            is None
        )

    def granted(expected: WorkspaceState, identity: WorkspaceIdentity) -> bool:
        # An interrupted plain artifact folder needs no Worker proof. A recorded Git
        # checkout does; missing or malformed evidence is conservatively treated as Git.
        def replay_requires_registration() -> bool:
            retired_root = ticket_files_root(db_path).parent / ".ticket-retired"
            try:
                for record_path in retired_root.glob("*.json"):
                    record = json.loads(record_path.read_text())
                    recorded_identity = (
                        record.get("ticket_id"),
                        record.get("machine_id"),
                        record.get("workspace_id"),
                    )
                    if recorded_identity != (
                        expected.ticket_id, identity.machine_id, identity.workspace_id
                    ):
                        continue
                    target = Path(record["target"])
                    if target.parent != retired_root or target.is_symlink():
                        return True
                    marker = json.loads((target / ".panels-workspace.json").read_text())
                    return (
                        marker.get("checkout_kind") == "clone"
                        or marker.get("repository") is not None
                    )
            except (OSError, ValueError, KeyError, TypeError):
                return True
            return True

        def check() -> Path | str:
            with closing(connect(db_path)) as fresh:
                fresh.execute("BEGIN IMMEDIATE")
                try:
                    authorized = custody_retired(
                        fresh, identity, require_registration=replay_requires_registration()
                    ) and (
                        has_committed_retirement(fresh, expected, identity)
                        or authorize_retirement(fresh, expected, now, identity)
                    )
                    fresh.commit()
                except BaseException:
                    fresh.rollback()
                    raise
                return Path(".") if authorized else "Local child retirement is not proven"

        result = (
            retire_local_children(expected.ticket_id, identity, check)
            if retire_local_children is not None
            else check()
        )
        return isinstance(result, Path)

    def detach(
        path: Path, expected: WorkspaceState, identity: WorkspaceIdentity, checkout_only: bool
    ) -> Path | str:
        target = prepare_workspace_retirement(
            path,
            expected,
            identity=identity,
            retired_root=ticket_files_root(db_path).parent / ".ticket-retired",
            checkout_only=checkout_only,
        )
        if isinstance(target, str):
            return target
        detached = False
        try:
            with closing(connect(db_path)) as fresh:
                fresh.execute("BEGIN IMMEDIATE")
                try:
                    is_git_checkout = (path / "worktree" / ".git").exists()
                    if not custody_retired(fresh, identity, require_registration=is_git_checkout):
                        fresh.rollback()
                        return "Local child retirement is not proven"
                    if not authorize_retirement(fresh, expected, now, identity):
                        fresh.rollback()
                        return "Ticket is active or no longer eligible"
                    if read_workspace_identity(path) != identity:
                        fresh.rollback()
                        return "Physical workspace identity changed"
                    detach_workspace(path, target, checkout_only=checkout_only)
                    detached = True
                    fresh.commit()
                    return target
                except BaseException:
                    if detached and target.exists():
                        if checkout_only:
                            (target / "worktree").rename(path / "worktree")
                            (target / ".panels-workspace.json").replace(
                                path / ".panels-workspace.json"
                            )
                        elif not path.exists():
                            target.rename(path)
                    fresh.rollback()
                    raise
        finally:
            if not detached:
                (target.parent / f"{target.name.rsplit('-', 1)[1]}.json").unlink(missing_ok=True)
                if checkout_only and target.is_dir() and not (target / "worktree").exists():
                    (target / ".panels-workspace.json").unlink(missing_ok=True)
                    target.rmdir()

    def retire(
        path: Path, expected: WorkspaceState, identity: WorkspaceIdentity, checkout_only: bool
    ) -> Path | str:
        def operation() -> Path | str:
            return detach(path, expected, identity, checkout_only)

        return (
            retire_local_children(expected.ticket_id, identity, operation)
            if retire_local_children is not None
            else operation()
        )

    result = cleanup_local_workspaces(
        ticket_files_root(db_path),
        states,
        now,
        extra_roots=extra_roots,
        active_ticket_ids=active,
        apply=apply,
        measure_size=measure_size,
        retire=retire,
        retirement_is_committed=granted,
    )
    return result
