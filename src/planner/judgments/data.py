"""The canonical read and write doors for Ticket judgment records."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.core.errors import ErrorCode, PlannerError
from planner.judgments.contracts import TicketJudgment, Verdict
from planner.judgments.logic.verdicts import normalize_verdict, require_finished_ticket


@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT judgment_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO judgment_write")
            conn.execute("RELEASE judgment_write")
            raise
        else:
            conn.execute("RELEASE judgment_write")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def read_ticket_judgment(
    conn: sqlite3.Connection, ticket_id: str
) -> TicketJudgment | None:
    row = conn.execute(
        "SELECT ticket_id, verdict_rating, verdict_text "
        "FROM ticket_judgments WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    return TicketJudgment(
        ticket_id=str(row["ticket_id"]),
        verdict_rating=(
            int(row["verdict_rating"]) if row["verdict_rating"] is not None else None
        ),
        verdict_text=(
            str(row["verdict_text"]) if row["verdict_text"] is not None else None
        ),
    )


def read_verdict(conn: sqlite3.Connection, ticket_id: str) -> Verdict | None:
    judgment = read_ticket_judgment(conn, ticket_id)
    if judgment is None or (
        judgment.verdict_rating is None and judgment.verdict_text is None
    ):
        return None
    return Verdict(rating=judgment.verdict_rating, text=judgment.verdict_text)


def upsert_verdict(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    rating: int | None,
    text: str | None,
) -> Verdict | None:
    verdict = normalize_verdict(rating, text)
    with _txn(conn):
        ticket = conn.execute(
            "SELECT stage FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None:
            raise PlannerError(
                ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id}
            )
        require_finished_ticket(str(ticket["stage"]))
        conn.execute(
            "INSERT INTO ticket_judgments (ticket_id, verdict_rating, verdict_text) "
            "VALUES (?, ?, ?) ON CONFLICT(ticket_id) DO UPDATE SET "
            "verdict_rating = excluded.verdict_rating, "
            "verdict_text = excluded.verdict_text",
            (ticket_id, verdict.rating, verdict.text),
        )
    if verdict.rating is None and verdict.text is None:
        return None
    return verdict
