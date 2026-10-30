"""Canonical persisted targets for executor tests, independent of a live proxy."""

import json
from collections.abc import Mapping

from planner.core.db import connect
from planner.executor.codec import encode_value
from planner.executor.contracts import AskKind, AskReference, JsonValue
from planner.executor.store import ExecutorStore


def persist_pending_turn(
    store: ExecutorStore,
    *asks: AskReference,
    runtime_overrides: Mapping[str, JsonValue] | None = None,
) -> None:
    """Install the assignment and turn that make an accepted ask owner-answerable."""
    first = asks[0]
    store.register_child(first.child, "ticket", "test-ticket", 1000)
    runtime: dict[str, JsonValue] = {
        "ended": False,
        "permission_provider_ids": {
            ask.ask_id: ask.ask_id for ask in asks if ask.kind == AskKind.permission
        },
        "user_input_provider_ids": {
            ask.ask_id: ask.ask_id for ask in asks if ask.kind == AskKind.user_input
        },
        "pending_permission_ask_ids": [
            ask.ask_id for ask in asks if ask.kind == AskKind.permission
        ],
        "pending_user_input_questions": {
            ask.ask_id: [] for ask in asks if ask.kind == AskKind.user_input
        },
    }
    runtime.update(runtime_overrides or {})
    connection = connect(store.db_path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO executor_core_turns VALUES (?,?,?,?)",
            (
                first.child.conversation_id,
                json.dumps(encode_value(first.child)),
                json.dumps(encode_value(first.turn)),
                json.dumps(runtime),
            ),
        )
    finally:
        connection.close()
