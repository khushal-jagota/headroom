"""Disposable files owned by one Worker backend process, on every platform."""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from planner.conversation.contracts import ResolvedConversationStart
from planner.files.logic.paths import ticket_files_root


class WorkerScratch:
    def __init__(self, environment: Mapping[str, str]) -> None:
        # The executor replaces this path with its own local Ticket root on Mac.
        root = Path(environment["PLAN_TICKET_FILES_DIR"]) / "tmp"
        root.mkdir(parents=True, exist_ok=True)
        self.directory = Path(tempfile.mkdtemp(prefix=".t", dir=root))
        self.environment = dict(environment)
        self.environment.update(dict.fromkeys(("TMPDIR", "TMP", "TEMP"), str(self.directory)))

    def close(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def with_worker_files_root(
    start: ResolvedConversationStart, db_path: str,
) -> ResolvedConversationStart:
    """Bind the current host path on every spawn, including persisted conversations."""
    role = start.role_materials
    if role is None:
        return start
    identity = dict(role.identity_environment_variables)
    if identity.get("PLAN_ACTOR") != "worker" or not identity.get("PLAN_TICKET_ID"):
        return start
    identity["PLAN_TICKET_FILES_DIR"] = str(ticket_files_root(db_path) / identity["PLAN_TICKET_ID"])
    return replace(start, role_materials=replace(
        role, identity_environment_variables=tuple(identity.items()),
    ))
