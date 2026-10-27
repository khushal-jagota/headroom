"""Freeze host input after a durable run claim, before any untrusted code starts."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from time import monotonic

from planner.core.errors import ErrorCode, PlannerError
from planner.credentials.contracts import ResolvedCredential
from planner.credentials.store import DatabaseCredentialResolver, FileCredentialCipher
from planner.measure_sources import data
from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import RunAdmission, RunMode, SourceRun
from planner.measure_sources.run_actions import current_definition
from planner.measures.data import latest_observations


def prepare_admission(
    conn: sqlite3.Connection, run: SourceRun, *, key_path: Path
) -> tuple[RunAdmission, tuple[ResolvedCredential, ...]]:
    # A coherent read snapshot covers source, grant metadata, and frozen prior inputs.
    conn.execute("BEGIN")
    try:
        if not current_definition(conn, run):
            raise PlannerError(ErrorCode.stale_claim, "source admission changed")
        source = data.read_source(conn, run.source_id)
        script = source.pending_script if run.mode is RunMode.test else source.active_script
        assert script is not None
        references = tuple(
            dict.fromkeys(g.credential for g in source.destinations if g.credential is not None)
        )
        credentials: tuple[ResolvedCredential, ...] = ()
        if references:
            cipher = FileCredentialCipher(key_path, has_records=True)
            resolver = DatabaseCredentialResolver(conn, cipher)
            credentials = tuple(
                resolver.resolve(source.scope, reference) for reference in references
            )
            for grant in source.destinations:
                if grant.credential is not None:
                    resolved = next(
                        c for c in credentials if c.metadata.reference == grant.credential
                    )
                    if grant.origin not in resolved.metadata.allowed_origins:
                        raise PlannerError(
                            ErrorCode.validation, "credential destination no longer granted"
                        )
        previous = latest_observations(
            conn, tuple(item.measure_id for item in run.required_outputs)
        )
        admission = RunAdmission(
            run=run,
            script_text=script.text,
            scope=source.scope,
            destinations=source.destinations,
            previous=previous,
            limits=DEFAULT_LIMITS,
            deadline_monotonic=monotonic() + DEFAULT_LIMITS.wall_seconds,
        )
    finally:
        conn.execute("ROLLBACK")
    return admission, credentials
