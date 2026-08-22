"""Closure contract for the one-way ACP conversation cutover."""

from __future__ import annotations

from pathlib import Path

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def test_only_acp_conversation_and_worker_self_routes_survive(tmp_path: Path) -> None:
    db_path = tmp_path / "route-closure.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))

    route_paths = set(app.openapi()["paths"])
    route_paths.update(str(path) for route in app.routes if (path := getattr(route, "path", None)))
    assert "/api/tickets/{ticket_id}/worker-self" in route_paths
    forbidden = (
        "/api/chat",
        "/api/employee-configuration-catalog",
        "/api/messages/chief",
        "/api/relay",
        "/api/tickets/by-live-session",
        "/api/tickets/by-employee-session",
        "/api/tickets/{ticket_id}/employee-session-history",
        "/files/chats",
    )
    assert not [path for path in route_paths if path.startswith(forbidden)]
    # A conversation is read over ordinary HTTP and tailed over the change stream. Panels
    # holds no socket open, so the bare path that was the WebSocket is now the prefix the
    # conversation's own routes live under, and there is no route at the path itself.
    assert "/api/conversation" not in route_paths
    assert "/api/conversation/conversations" in route_paths


def test_fresh_schema_has_employee_correctness_without_legacy_chat_tables(
    tmp_path: Path,
) -> None:
    conn = connect(str(tmp_path / "schema-closure.db"))
    try:
        create_schema(conn)
        tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {
            "chat_messages",
            "chat_turns",
            "chat_turn_activity_entries",
            "agent_chat_sessions",
            # The conversation layer that came before this one left five tables behind.
            "conversation_session_bindings",
            "employee_conversations",
            "employee_configuration_catalog_cache",
            "employee_step_runs",
            "ticket_conversation_projections",
        }.isdisjoint(tables)
        day_columns = {
            str(row["name"]) for row in conn.execute("PRAGMA table_info(days)").fetchall()
        }
        assert "chat_session_key" not in day_columns
    finally:
        conn.close()
