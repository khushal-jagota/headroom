"""Capture a source run and resolve named credentials only when the script uses them."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from time import monotonic

from planner.core.errors import ErrorCode, PlannerError
from planner.credentials.contracts import (
    CredentialReference,
    CredentialResolver,
    ResolvedCredential,
)
from planner.credentials.store import DatabaseCredentialResolver, FileCredentialCipher
from planner.measure_sources import data
from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import RunAdmission, RuntimeLimits, SourceRun
from planner.measure_sources.run_actions import current_definition
from planner.measures.data import latest_observations


class _LazyCredentialResolver:
    def __init__(self, conn: sqlite3.Connection, key_path: Path) -> None:
        self.conn = conn
        self.key_path = key_path

    def resolve(self, reference: CredentialReference) -> ResolvedCredential:
        cipher = FileCredentialCipher(self.key_path, has_records=True)
        return DatabaseCredentialResolver(self.conn, cipher).resolve(reference)


def prepare_admission(
    conn: sqlite3.Connection,
    run: SourceRun,
    *,
    key_path: Path,
    limits: RuntimeLimits = DEFAULT_LIMITS,
) -> tuple[RunAdmission, CredentialResolver]:
    conn.execute("BEGIN")
    try:
        if not current_definition(conn, run):
            raise PlannerError(ErrorCode.stale_claim, "source admission changed")
        source = data.read_source(conn, run.source_id)
        script = source.active_script
        assert script is not None
        previous = latest_observations(
            conn, tuple(item.measure_id for item in run.required_outputs)
        )
        admission = RunAdmission(
            run=run,
            script_text=script.text,
            previous=previous,
            limits=limits,
            deadline_monotonic=monotonic() + limits.wall_seconds,
        )
    finally:
        conn.execute("ROLLBACK")
    return admission, _LazyCredentialResolver(conn, key_path)
