"""Sprint date integrity and Outcome behavior."""

from __future__ import annotations

from sqlite3 import Connection

_EMPTY_CODING_FIELDS = "{}"


def _insert_ticket(
    conn: Connection,
    ticket_id: str,
    stage: str,
    *,
    sprint_item_id: str | None = None,
    worker_step_claim: str = "none",
) -> None:
    # T04 owns ticket writers; a direct INSERT is the sanctioned test-fixture
    # shortcut for setting child/blocker ticket-states (only NOT-NULL non-defaulted
    # columns are supplied; project may stay NULL under its CHECK).
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, "
        "sprint_item_id, ceiling, "
        "worker_step_claim, field_values, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'hermes', ?, ?, 'needs_success', ?, ?, 0, 0)",
        (ticket_id, "child", stage, sprint_item_id, worker_step_claim, _EMPTY_CODING_FIELDS),
    )


# --- item 10: sprint-item permissions (single anchored test) ----------------------


# --- item 20: sprint overlap (single anchored test) -------------------------------




# --- supplementary sprints-domain tests (no fence anchor) -------------------------
