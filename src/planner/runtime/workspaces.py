"""Application workspace defaults scoped to the canonical main database."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from types import TracebackType

_lock = threading.Lock()
_registrations: dict[str, WorkspaceRegistration] = {}


class WorkspaceRegistration:
    def __init__(self, identity: str, workspace_root: Path | None) -> None:
        self.identity = identity
        self.workspace_root = workspace_root
        self.closed = False

    def close(self) -> None:
        with _lock:
            if self.closed:
                return
            if _registrations.get(self.identity) is not self:
                raise RuntimeError("workspace registration changed")
            del _registrations[self.identity]
            self.closed = True

    def __enter__(self) -> WorkspaceRegistration:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def register_workspace_root(
    database_path: str, workspace_root: Path | None
) -> WorkspaceRegistration:
    identity = str(Path(database_path).resolve())
    with _lock:
        if identity in _registrations:
            raise RuntimeError("workspace database is already registered")
        registration = WorkspaceRegistration(identity, workspace_root)
        _registrations[identity] = registration
        return registration


def workspace_root_for_connection(connection: sqlite3.Connection) -> Path | None:
    identity = None
    for row in connection.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            identity = str(Path(row[2]).resolve())
            break
    with _lock:
        registration = _registrations.get(identity) if identity is not None else None
        return registration.workspace_root if registration is not None else None
