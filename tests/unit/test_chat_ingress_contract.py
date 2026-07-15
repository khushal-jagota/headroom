"""Static and HTTP locks for the one canonical human Chat ingress."""

from __future__ import annotations

import ast
import inspect
from dataclasses import fields
from pathlib import Path

from fastapi.testclient import TestClient

from planner.chat.contracts import (
    ChatState,
    ChatTurn,
    HumanChatCompletion,
    HumanChatOutputDelta,
)
from planner.core.adapters.base import GatewayAdapter
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

ROOT = Path(__file__).resolve().parents[2]


def _tree(path: str) -> ast.Module:
    return ast.parse((ROOT / path).read_text(encoding="utf-8"))


def _top_level_functions(path: str) -> set[str]:
    return {
        node.name
        for node in _tree(path).body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def _class_methods(path: str, class_name: str) -> set[str]:
    class_node = next(
        node
        for node in _tree(path).body
        if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return {
        node.name
        for node in class_node.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    }


def test_retired_chat_ingress_routes_are_absent(tmp_path: Path) -> None:
    db_path = tmp_path / "chat-ingress.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        build_adapters(config),
        lambda: connect(str(db_path)),
    )

    route_keys = {
        (path, method.upper())
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }
    assert ("/api/chat/{entity_id}/turns", "POST") in route_keys
    assert ("/api/messages/chief", "POST") in route_keys
    assert ("/api/chat/{entity_id}/history", "GET") not in route_keys
    assert ("/api/tickets/{ticket_id}/employee-session-history", "GET") in route_keys
    assert ("/api/tickets/by-session/{session_key}", "GET") not in route_keys
    assert (
        "/api/tickets/by-employee-session/{employee_session_id}",
        "GET",
    ) in route_keys
    assert ("/api/tickets/by-live-session/{live_session_id}", "GET") in route_keys
    for retired_path in (
        "/api/chat/{entity_id}/send",
        "/api/chat/{entity_id}/stream",
        "/api/chat/{entity_id}/command",
    ):
        assert (retired_path, "POST") not in route_keys
    with TestClient(app) as client:
        for suffix, body in (
            ("send", {"text": "hello"}),
            ("stream", {"text": "hello", "mode": "message"}),
            ("command", {"command": "/status"}),
        ):
            assert (
                client.post(f"/api/chat/day_2099-01-01/{suffix}", json=body).status_code
                == 404
            )


def test_retired_chat_services_results_and_adapter_methods_are_absent() -> None:
    assert {"send", "stream", "run_command", "history"}.isdisjoint(
        _top_level_functions("src/planner/chat/service.py")
    )
    contract_classes = {
        node.name
        for node in _tree("src/planner/chat/contracts.py").body
        if isinstance(node, ast.ClassDef)
    }
    assert {
        "ChatSendResult",
        "CommandRunResult",
        "ChatMessage",
        "ChatHistory",
        "ChatHistoryMessage",
        "ChatHistoryResponse",
    }.isdisjoint(contract_classes)

    implementations = (
        ("src/planner/core/adapters/base.py", "GatewayAdapter"),
        ("src/planner/core/adapters/fakes.py", "EchoGatewayAdapter"),
        ("src/planner/core/adapters/fakes.py", "OfflineGatewayAdapter"),
        ("src/planner/core/adapters/real.py", "RealGatewayAdapter"),
        ("src/planner/minds/shared_gateway.py", "EntityRoutingGateway"),
        ("src/planner/minds/shared_gateway.py", "SharedGateway"),
    )
    for path, class_name in implementations:
        assert {"send", "stream", "run_command"}.isdisjoint(
            _class_methods(path, class_name)
        )
        assert "run_human_turn" in _class_methods(path, class_name)
        assert "history" not in _class_methods(path, class_name)
        assert "read_employee_session_history" in _class_methods(path, class_name)


def test_surviving_gateway_observation_contract_is_exact() -> None:
    assert list(inspect.signature(GatewayAdapter.run_human_turn).parameters) == [
        "self",
        "session_key",
        "entity_id",
        "text",
        "mode",
        "bind_session_key",
        "image_paths",
        "require_existing_session",
    ]
    assert [field.name for field in fields(HumanChatOutputDelta)] == ["text"]
    assert [field.name for field in fields(HumanChatCompletion)] == ["text", "role"]
    assert [field.name for field in fields(ChatState)] == [
        "messages",
        "outcomes",
        "active_turn",
    ]
    turn_fields = [field.name for field in fields(ChatTurn)]
    assert "can_pause" in turn_fields
    assert "session_key" not in turn_fields


def test_http_sse_browser_helper_and_live_documentation_are_absent() -> None:
    api_source = (ROOT / "src/planner/chat/api.py").read_text(encoding="utf-8")
    for retired in (
        '@router.post("/chat/{entity_id}/send")',
        '@router.post("/chat/{entity_id}/stream")',
        '@router.post("/chat/{entity_id}/command")',
        "StreamingResponse",
        "def _sse",
        "text/event-stream",
    ):
        assert retired not in api_source

    browser_source = (ROOT / "web/src/lib/api.ts").read_text(encoding="utf-8")
    for retired in ("streamChat", "parseSseChunk", "SseEvent", "/stream"):
        assert retired not in browser_source

    built_assets = tuple((ROOT / "web/dist/assets").glob("index-*.js"))
    assert built_assets
    live_paths = (
        ROOT / "docs/chat.md",
        ROOT / "docs/systems.md",
        ROOT / "docs/systems.html",
        *built_assets,
    )
    live_text = "\n".join(path.read_text(encoding="utf-8") for path in live_paths)
    for retired in (
        "/api/chat/{entity_id}/send",
        "/api/chat/{entity_id}/stream",
        "/api/chat/{entity_id}/command",
        "ChatSendResult",
        "CommandRunResult",
        "streamChat",
    ):
        assert retired not in live_text
