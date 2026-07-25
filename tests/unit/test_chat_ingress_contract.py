"""Closure contract for the one-way ACP conversation cutover."""

from __future__ import annotations

from pathlib import Path

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

ROOT = Path(__file__).resolve().parents[2]


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
    route_paths.update(
        str(path) for route in app.routes if (path := getattr(route, "path", None))
    )
    assert "/api/conversation" in route_paths
    assert "/api/tickets/{ticket_id}/worker-self" in route_paths
    forbidden = (
        "/api/chat",
        "/api/messages/chief",
        "/api/relay",
        "/api/tickets/by-live-session",
        "/api/tickets/by-employee-session",
        "/api/tickets/{ticket_id}/employee-session-history",
        "/files/chats",
    )
    assert not [path for path in route_paths if path.startswith(forbidden)]


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
        assert "employee_step_runs" in tables
        assert {
            "chat_messages",
            "chat_turns",
            "chat_turn_activity_entries",
            "agent_chat_sessions",
        }.isdisjoint(tables)
        day_columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(days)").fetchall()
        }
        assert "chat_session_key" not in day_columns
    finally:
        conn.close()


def test_deleted_python_owners_and_live_imports_are_absent() -> None:
    for relative in (
        "src/planner/chat",
        "src/planner/minds",
        "src/planner/hermes_backend",
        "src/planner/core/adapters",
        "src/planner/tickets/employee_session_history.py",
        "src/planner/files/chat_images.py",
    ):
        path = ROOT / relative
        assert not path.exists() or not any(path.rglob("*.py"))

    source_paths = tuple((ROOT / "src/planner").rglob("*.py"))
    live_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in source_paths
        if path != ROOT / "src/planner/core/db.py"
    )
    for token in (
        "planner.chat",
        "planner.minds",
        "planner.hermes_backend",
        "planner.core.adapters",
        "SharedGateway",
        "PoolStepGateway",
        "PLAN_GATEWAY_ADAPTER",
        "PLAN_RELAY_BACKEND_ENABLED",
        "PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE",
    ):
        assert token not in live_source


def test_config_and_served_build_have_no_legacy_switch_or_route() -> None:
    config_text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    for token in (
        "gateway_adapter",
        "relay_backend_enabled",
        "run_startup_recovery_in_test_mode",
        "hermes_bin",
        "hermes_profile",
        "worker_skill",
    ):
        assert token not in config_text

    built_assets = tuple((ROOT / "web/dist/assets").glob("index-*.js"))
    assert built_assets
    built_text = "\n".join(path.read_text(encoding="utf-8") for path in built_assets)
    for token in (
        "/api/chat",
        "/api/messages/chief",
        "/api/relay",
        "/files/chats",
        "ChatPanel",
        "ChiefNeutralPane",
    ):
        assert token not in built_text
