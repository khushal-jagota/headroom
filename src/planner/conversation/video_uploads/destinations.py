"""Resolve upload destinations through the existing conversation access rules."""

import sqlite3
from collections.abc import Callable

from fastapi import HTTPException

from planner.conversation.api import (
    _require_mutable_conversation,
    _require_unassociated_send_conversation,
)
from planner.conversation.runtime_composition import ConversationRuntime
from planner.conversation.video_uploads.contracts import UploadDestination
from planner.managers import data as managers_data
from planner.tickets import data as tickets_data


async def authorize_destination(
    destination: UploadDestination,
    *,
    conn_factory: Callable[[], sqlite3.Connection],
    runtime: ConversationRuntime | None,
) -> None:
    conn = conn_factory()
    try:
        if destination.kind == "ticket":
            tickets_data.read_ticket(conn, destination.id)
        elif destination.kind == "chief":
            if destination.id != "chief":
                raise HTTPException(status_code=422, detail="invalid Chief destination")
        elif destination.kind == "manager":
            managers_data.read_manager(conn, destination.id)
        else:
            if runtime is None:
                raise HTTPException(status_code=503, detail="conversation system is unavailable")
            _require_mutable_conversation(runtime, destination.id)
            _require_unassociated_send_conversation(runtime, destination.id)
            if await runtime.store.read_conversation(destination.id) is None:
                raise HTTPException(status_code=404, detail="no such conversation")
    finally:
        conn.close()
