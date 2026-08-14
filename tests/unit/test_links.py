from __future__ import annotations

from sqlite3 import Connection

import pytest

from planner.core import links as core_links
from planner.core.contracts import (
    BlockedBySummaryRow,
    BlockerSummary,
    BlocksTargetSummaryRow,
    LinkKind,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import views as ticket_views
from planner.tickets.contracts import TicketFields
from planner.tickets.logic.fields_codec import fields_to_json
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

_EMPTY_CODING_FIELDS = fields_to_json(TicketFields.empty(CODING_WORKER_TYPE_DEFINITION.field_ids()))


def _ticket(conn: Connection, ticket_id: str, stage: str = "needs_success") -> None:
    captured_default = None if stage in {"done", "dropped"} else "worker"
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, ceiling, "
        "default_stage_ownership_mode, fields, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'hermes', ?, 'needs_success', ?, ?, 1, 1)",
        (ticket_id, ticket_id, stage, captured_default, _EMPTY_CODING_FIELDS),
    )


def _sprint_item(conn: Connection, item_id: str) -> None:
    conn.execute(
        "INSERT INTO sprint_items (id, title, project_id, created_at, updated_at) "
        "VALUES (?, ?, 'project_vylo', 1, 1)",
        (item_id, item_id),
    )


def test_add_blocks_link_requires_real_ticket_source_and_real_target(
    tmp_db: Connection,
) -> None:
    _ticket(tmp_db, "t_source")
    _ticket(tmp_db, "t_target")
    _sprint_item(tmp_db, "si_target")

    core_links.add_link(tmp_db, "t_source", "t_target", LinkKind.blocks, 1)
    core_links.add_link(tmp_db, "t_source", "si_target", LinkKind.blocks, 1)

    cases = [
        ("t_missing", "t_target", "from_id"),
        ("si_target", "t_target", "from_id"),
        ("t_source", "t_missing", "to_id"),
        ("t_source", "si_missing", "to_id"),
    ]
    for from_id, to_id, missing_field in cases:
        with pytest.raises(PlannerError) as exc:
            core_links.add_link(tmp_db, from_id, to_id, LinkKind.blocks, 1)
        assert exc.value.code is ErrorCode.link_invalid
        assert exc.value.detail[missing_field] in {from_id, to_id}


def test_add_blocks_endpoint_reads_happen_under_begin_immediate(
    tmp_db: Connection,
) -> None:
    _ticket(tmp_db, "t_source")
    _ticket(tmp_db, "t_target")
    statements: list[str] = []
    tmp_db.set_trace_callback(lambda statement: statements.append(statement.strip()))

    core_links.add_link(tmp_db, "t_source", "t_target", LinkKind.blocks, 1)

    begin_index = statements.index("BEGIN IMMEDIATE")
    under_lock = [statement.upper() for statement in statements[begin_index:]]
    assert any("SELECT 1 FROM TICKETS WHERE ID" in statement for statement in under_lock)
    assert any("INSERT INTO LINKS" in statement for statement in under_lock)


def test_blocks_cycle_check_ignores_inactive_sources(tmp_db: Connection) -> None:
    _ticket(tmp_db, "t_done_source", stage="done")
    _ticket(tmp_db, "t_active_target")

    core_links.add_link(tmp_db, "t_done_source", "t_active_target", LinkKind.blocks, 1)
    core_links.add_link(tmp_db, "t_active_target", "t_done_source", LinkKind.blocks, 1)

    assert [
        tuple(row)
        for row in tmp_db.execute("SELECT from_id, to_id, kind FROM links ORDER BY from_id")
    ] == [
        ("t_active_target", "t_done_source", "blocks"),
        ("t_done_source", "t_active_target", "blocks"),
    ]


def test_blocker_summary_resolves_active_and_cleared_ticket_and_item_rows(
    tmp_db: Connection,
) -> None:
    _ticket(tmp_db, "t_blocked", stage="needs_success")
    _ticket(tmp_db, "t_active_blocker", stage="needs_plan")
    _ticket(tmp_db, "t_done_blocker", stage="done")
    _ticket(tmp_db, "t_outgoing_source", stage="needs_success")
    _ticket(tmp_db, "t_outgoing_target", stage="needs_implementation")
    _sprint_item(tmp_db, "si_outgoing_target")
    tmp_db.execute("UPDATE tickets SET title = 'Blocked ticket' WHERE id = 't_blocked'")
    tmp_db.execute("UPDATE tickets SET title = 'Active blocker' WHERE id = 't_active_blocker'")
    tmp_db.execute("UPDATE tickets SET title = 'Done blocker' WHERE id = 't_done_blocker'")
    tmp_db.execute("UPDATE tickets SET title = 'Outgoing target' WHERE id = 't_outgoing_target'")
    tmp_db.execute(
        "UPDATE sprint_items SET title = 'Outgoing item' WHERE id = 'si_outgoing_target'"
    )

    core_links.add_link(tmp_db, "t_active_blocker", "t_blocked", LinkKind.blocks, 1)
    core_links.add_link(tmp_db, "t_done_blocker", "t_blocked", LinkKind.blocks, 1)
    core_links.add_link(tmp_db, "t_outgoing_source", "t_outgoing_target", LinkKind.blocks, 1)
    core_links.add_link(tmp_db, "t_outgoing_source", "si_outgoing_target", LinkKind.blocks, 1)

    assert core_links.blocker_summary(tmp_db, "t_blocked") == BlockerSummary(
        blocked=True,
        blocked_by=(
            BlockedBySummaryRow(
                ticket_id="t_active_blocker",
                title="Active blocker",
                stage="needs_plan",
                active=True,
                href="#/workspace/t_active_blocker",
            ),
            BlockedBySummaryRow(
                ticket_id="t_done_blocker",
                title="Done blocker",
                stage="done",
                active=False,
                href="#/workspace/t_done_blocker",
            ),
        ),
        blocks=(),
    )
    assert core_links.blocker_summary(tmp_db, "t_outgoing_source") == BlockerSummary(
        blocked=False,
        blocked_by=(),
        blocks=(
            BlocksTargetSummaryRow(
                target_id="si_outgoing_target",
                target_kind="sprint_item",
                title="Outgoing item",
                active=True,
                href="#/sprint?item=si_outgoing_target",
            ),
            BlocksTargetSummaryRow(
                target_id="t_outgoing_target",
                target_kind="ticket",
                title="Outgoing target",
                active=True,
                href="#/workspace/t_outgoing_target",
            ),
        ),
    )


def test_ticket_detail_and_copy_text_use_resolved_blocker_summary(
    tmp_db: Connection,
) -> None:
    _ticket(tmp_db, "t_blocked", stage="needs_success")
    _ticket(tmp_db, "t_active_blocker", stage="needs_plan")
    _ticket(tmp_db, "t_done_blocker", stage="done")
    tmp_db.execute("UPDATE tickets SET title = 'Blocked ticket' WHERE id = 't_blocked'")
    tmp_db.execute("UPDATE tickets SET title = 'Active blocker' WHERE id = 't_active_blocker'")
    tmp_db.execute("UPDATE tickets SET title = 'Done blocker' WHERE id = 't_done_blocker'")
    core_links.add_link(tmp_db, "t_active_blocker", "t_blocked", LinkKind.blocks, 1)
    core_links.add_link(tmp_db, "t_done_blocker", "t_blocked", LinkKind.blocks, 1)

    detail = ticket_views.ticket_detail(tmp_db, "t_blocked", 1)

    assert detail["blocked"] is True
    assert detail["blocker_summary"] == {
        "blocked_by": [
            {
                "ticket_id": "t_active_blocker",
                "title": "Active blocker",
                "stage": "needs_plan",
                "href": "#/workspace/t_active_blocker",
            }
        ]
    }
    assert "links" not in detail
    copy_text = ticket_views.copy_text(tmp_db, "t_blocked")
    assert "blocked_by:" in copy_text
    assert "- Active blocker (t_active_blocker, needs_plan)" in copy_text
    assert "Done blocker" not in copy_text
    assert "blocks:" not in copy_text

    cleared_detail = ticket_views.ticket_detail(tmp_db, "t_done_blocker", 1)
    assert "blocker_summary" not in cleared_detail
