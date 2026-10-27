"""Source failure episodes feed the existing durable conversation notice system."""

from __future__ import annotations

import sqlite3

from planner.measure_sources import data
from planner.measure_sources.contracts import (
    FailureCode,
    FailureNoticeIdentity,
    MeasureSource,
    RunFailure,
    SourceRun,
)

FAILURE_MESSAGES = {
    FailureCode.sandbox_unavailable: "The isolated runtime is unavailable.",
    FailureCode.runtime_failure: "The source script failed.",
    FailureCode.malformed_protocol: "The source sent an invalid host message.",
    FailureCode.unauthorized_request: "A request exceeded the source's granted destinations.",
    FailureCode.credential_unavailable: "A required credential is unavailable.",
    FailureCode.transport_failure: "The provider request failed.",
    FailureCode.http_error: "The provider returned an HTTP error.",
    FailureCode.invalid_json: "The provider returned invalid JSON.",
    FailureCode.invalid_output: "The source returned invalid observations.",
    FailureCode.missing_output: "The source omitted required current observations.",
    FailureCode.stale_definition: "The source or an output definition changed during the run.",
    FailureCode.budget_exhausted: "The run exceeded a resource limit.",
    FailureCode.interrupted: "The collection stopped before completion.",
}


def failure_message(failure: RunFailure | None) -> str | None:
    if failure is None:
        return None
    message = FAILURE_MESSAGES[failure.code]
    if failure.budget is not None:
        message += f" Limit: {failure.budget.value}."
    if failure.http_status is not None:
        message += f" HTTP status: {failure.http_status}."
    return message


def notify_episode(conn: sqlite3.Connection, source: MeasureSource, *, now: int) -> None:
    episode = data.read_failure_episode(conn, source.id)
    if episode is None:
        return
    run = data.read_run(conn, episode.latest_run_id)
    project = conn.execute(
        "SELECT name FROM projects WHERE id=?", (source.scope.project_id,)
    ).fetchone()
    project_name = str(project[0]) if project is not None else source.scope.project_id
    last_success = conn.execute(
        "SELECT finished_at FROM measure_source_runs WHERE source_id=? "
        "AND mode='collection' AND status='succeeded' ORDER BY finished_at DESC,rowid DESC LIMIT 1",
        (source.id,),
    ).fetchone()
    last_success_text = "none" if last_success is None else str(last_success[0])
    affected = ", ".join(dict.fromkeys(output.required_date for output in run.required_outputs[:5]))
    summary = (
        f"Measure source {source.name!r} in {project_name} / {source.scope.account} failed. "
        f"{failure_message(run.failure)} "
        f"Last successful collection timestamp: {last_success_text}. Affected dates: {affected}. "
        f"Inspect source {source.id} and run {run.id}. "
        "Assign a repair Ticket if script work is needed. Credential "
        "replacement uses the owner form. "
        "Escalate unresolved problems through existing messaging or the repair Ticket help route. "
        "Only a successful collection of the published revision restores source health."
    )
    from planner.manager_wakes import data as wake_data
    from planner.manager_wakes.contracts import WakeSourceKind

    wake_data.create_wake(
        conn,
        manager_id=source.responsible.id if source.responsible.kind.value == "manager" else None,
        ticket_id=None,
        source_kind=WakeSourceKind.source_failure,
        source_revision=episode.episode,
        summary=summary,
        now=now,
        target=source.responsible,
        source_identity=FailureNoticeIdentity(
            source.id, episode.episode, source.responsibility_revision, source.responsible
        ),
    )


def record_failure(conn: sqlite3.Connection, run: SourceRun, *, now: int) -> None:
    episode = data.read_failure_episode(conn, run.source_id)
    if episode is None:
        number = int(
            conn.execute(
                "SELECT COALESCE(MAX(episode),0)+1 FROM "
                "measure_source_failure_episodes WHERE source_id=?",
                (run.source_id,),
            ).fetchone()[0]
        )
        conn.execute(
            "INSERT INTO measure_source_failure_episodes(source_id,episode,"
            "opened_at,latest_run_id) "
            "VALUES (?,?,?,?)",
            (run.source_id, number, now, run.id),
        )
    else:
        conn.execute(
            "UPDATE measure_source_failure_episodes SET latest_run_id=? WHERE "
            "source_id=? AND episode=?",
            (run.id, run.source_id, episode.episode),
        )
    notify_episode(conn, data.read_source(conn, run.source_id), now=now)


def notice_is_current(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    source = conn.execute(
        "SELECT responsible_kind,responsible_manager_id,responsibility_revision "
        "FROM measure_sources WHERE id=?",
        (row["measure_source_id"],),
    ).fetchone()
    if source is None:
        return False
    target_id = (
        source["responsible_manager_id"] if source["responsible_kind"] == "manager" else "chief"
    )
    if (source["responsible_kind"], target_id, source["responsibility_revision"]) != (
        row["target_kind"],
        row["target_id"],
        row["source_responsibility_revision"],
    ):
        return False
    return (
        conn.execute(
            "SELECT 1 FROM measure_source_failure_episodes WHERE source_id=? "
            "AND episode=? AND closed_at IS NULL",
            (row["measure_source_id"], row["source_episode"]),
        ).fetchone()
        is not None
    )
