"""Source/run storage reads and transaction-owned shared writes."""

from __future__ import annotations

import sqlite3

from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.credentials.contracts import CredentialScope
from planner.measure_sources.contracts import (
    FailureEpisode,
    MeasureSource,
    RunMode,
    RunStatus,
    RunTrigger,
    ScriptRevision,
    SourceRun,
    SourceRunDetail,
)
from planner.measure_sources.logic.health import collection_health
from planner.measure_sources.records import (
    decode_candidates,
    decode_failure,
    decode_metrics,
    decode_outputs,
)
from planner.measures.contracts import HistoryWindow, SourceHealthSummary


def _script(row: sqlite3.Row, prefix: str) -> ScriptRevision | None:
    if row[f"{prefix}_script"] is None:
        return None
    return ScriptRevision(
        revision=int(row[f"{prefix}_script_revision"]),
        text=str(row[f"{prefix}_script"]),
        sha256=str(row[f"{prefix}_script_sha256"]),
        authored_by=Principal(
            PrincipalKind(row[f"{prefix}_script_author_kind"]),
            str(row[f"{prefix}_script_author_id"]),
        ),
        created_at=int(row[f"{prefix}_script_created_at"]),
    )


def read_source(conn: sqlite3.Connection, source_id: str) -> MeasureSource:
    row = conn.execute("SELECT * FROM measure_sources WHERE id=?", (source_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "measure source does not exist")
    return MeasureSource(
        id=str(row["id"]),
        name=str(row["name"]),
        scope=CredentialScope(str(row["project_id"]), str(row["account"])),
        responsible=CHIEF_PRINCIPAL
        if row["responsible_kind"] == "chief"
        else Principal(PrincipalKind.manager, str(row["responsible_manager_id"])),
        responsibility_revision=int(row["responsibility_revision"]),
        enabled=bool(row["enabled"]),
        local_time=row["local_time"],
        source_revision=int(row["source_revision"]),
        active_script=_script(row, "active"),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def bump_source_revision(conn: sqlite3.Connection, source_id: str, *, now: int) -> None:
    if not conn.in_transaction:
        raise RuntimeError("source revision requires the caller transaction")
    changed = conn.execute(
        "UPDATE measure_sources SET source_revision=source_revision+1,updated_at=? WHERE id=?",
        (now, source_id),
    )
    if changed.rowcount != 1:
        raise PlannerError(ErrorCode.not_found, "measure source does not exist")


def read_run(conn: sqlite3.Connection, run_id: str) -> SourceRun:
    row = conn.execute("SELECT * FROM measure_source_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "source run does not exist")
    return SourceRun(
        id=str(row["id"]),
        source_id=str(row["source_id"]),
        mode=RunMode(row["mode"]),
        trigger=RunTrigger(row["trigger"]),
        status=RunStatus(row["status"]),
        source_revision=int(row["source_revision"]),
        script_revision=int(row["script_revision"]),
        script_sha256=str(row["script_sha256"]),
        frozen_at=int(row["frozen_at"]),
        required_outputs=decode_outputs(str(row["required_outputs"])),
        history=None
        if row["history_first_date"] is None
        else HistoryWindow(str(row["history_first_date"]), str(row["history_last_date"])),
        schedule_date=row["schedule_date"],
        schedule_time=row["schedule_time"],
        created_at=int(row["created_at"]),
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        failure=decode_failure(row["failure"]),
        metrics=decode_metrics(row["metrics"]),
        observation_count=int(row["observation_count"]),
    )


def read_run_detail(conn: sqlite3.Connection, run_id: str) -> SourceRunDetail:
    run = read_run(conn, run_id)
    row = conn.execute(
        "SELECT test_candidates FROM measure_source_runs WHERE id=?", (run_id,)
    ).fetchone()
    assert row is not None
    return SourceRunDetail(run, decode_candidates(row[0]))


def read_failure_episode(conn: sqlite3.Connection, source_id: str) -> FailureEpisode | None:
    row = conn.execute(
        "SELECT * FROM measure_source_failure_episodes WHERE source_id=? AND closed_at IS NULL",
        (source_id,),
    ).fetchone()
    return (
        None
        if row is None
        else FailureEpisode(
            str(row["source_id"]),
            int(row["episode"]),
            int(row["opened_at"]),
            str(row["latest_run_id"]),
            row["closed_at"],
        )
    )


def read_health(
    conn: sqlite3.Connection, source_ids: tuple[str, ...], *, now: int
) -> tuple[SourceHealthSummary, ...]:
    values: list[SourceHealthSummary] = []
    for source_id in dict.fromkeys(source_ids):
        source = read_source(conn, source_id)
        success = conn.execute(
            "SELECT frozen_at,finished_at FROM measure_source_runs WHERE source_id=? "
            "AND mode='collection' AND status='succeeded' "
            "ORDER BY frozen_at DESC,rowid DESC LIMIT 1",
            (source_id,),
        ).fetchone()
        episode = read_failure_episode(conn, source_id)
        values.append(
            SourceHealthSummary(
                source_id=source.id,
                source_name=source.name,
                health=collection_health(
                    enabled=source.enabled,
                    failed=episode is not None,
                    latest_success_frozen_at=None if success is None else int(success[0]),
                ),
                latest_success_at=None if success is None else int(success[1]),
                latest_failure_run_id=None if episode is None else episode.latest_run_id,
            )
        )
    return tuple(values)
