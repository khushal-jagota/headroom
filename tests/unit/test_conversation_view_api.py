"""Selected-view HTTP join and demand boundaries over the existing real runtime harness."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from tests.unit.test_conversation_api import (
    CONVERSATION_PREFIX,
    _EventStreamDrive,
    _Harness,
    _run,
    _start,
)
from tests.unit.test_conversation_api import (
    harness as harness,
)

from planner.conversation.events import AgentMessageEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.views.api import router
from planner.core import db


def test_focus_tail_and_rest_deliver_no_hidden_prose_and_receipts_survive_old_page(
    harness: _Harness,
) -> None:
    harness.app.include_router(router, prefix=CONVERSATION_PREFIX)

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            response = await client.post(
                f"{CONVERSATION_PREFIX}/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "Owner asks"}],
                    "sender_label": "owner",
                    "sender_message_id": "receipt-one",
                },
            )
            assert response.status_code == 200
            path = f"{CONVERSATION_PREFIX}/conversations/c/view"
            async with _EventStreamDrive(harness.app, path + "/tail", "lens=focus") as stream:
                name, snapshot = await stream.next_named_frame()
                assert name == "snapshot"
                assert len(snapshot["page"]["items"]) <= 100
                stored = await harness.store.append_event(
                    "c",
                    AgentMessageEventPayload(content=text_message_content("private commentary")),
                )
                harness.live_tail.publish_event(stored)
                name, change = await stream.next_named_frame()
                assert name == "changes"
                assert change["coverage"]["through_sequence"] == stored.sequence
                assert "private commentary" not in json.dumps(change)
                assert change["status"]["live_output"] is None
            rest = await client.get(path, params={"transcript": "false"})
            assert rest.status_code == 200 and rest.json()["page"]["items"] == []
            receipts = await client.get(
                path + "/receipts", params={"message_ids": json.dumps(["receipt-one"])}
            )
            assert receipts.status_code == 200
            assert receipts.json()["receipts"][0]["sender_message_id"] == "receipt-one"
            permission = await harness.raise_permission_ask("c")
            user_input = await harness.request_user_input("c")
            status = (await client.get(path, params={"transcript": "false"})).json()["status"]
            assert status["pending_permission"]["request_id"] == permission
            assert status["pending_user_input"]["request_id"] == user_input.request_id
            before = (await client.get(f"{CONVERSATION_PREFIX}/conversations/c")).json()[
                "owner_read_through_sequence"
            ]
            assert (await client.get(path + "/pages")).status_code == 200
            after = (await client.get(f"{CONVERSATION_PREFIX}/conversations/c")).json()[
                "owner_read_through_sequence"
            ]
            assert before == after

    _run(exercise)


def test_selected_body_stream_is_exact_and_obsolete_reference_is_409(harness: _Harness) -> None:
    from planner.core.errors import PlannerError
    from planner.core.server import handle_planner_error

    harness.app.add_exception_handler(PlannerError, handle_planner_error)
    harness.app.include_router(router, prefix=CONVERSATION_PREFIX)

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            text = "# Heading\n\n" + "界🙂" * 40000
            await harness.store.append_event(
                "c", AgentMessageEventPayload(content=text_message_content(text))
            )
            path = f"{CONVERSATION_PREFIX}/conversations/c/view"
            page = (await client.get(path, params={"lens": "full"})).json()["page"]
            row = next(item for item in page["items"] if item.get("row_kind") == "agent_message")
            reference = row["body"]["reference"]
            response = await client.get(
                path + "/body/content", params={"reference": json.dumps(reference)}
            )
            assert response.status_code == 200
            assert response.json()["content"][0]["text"] == text
            original_version = reference["version"]
            reference["version"] = "obsolete"
            stale = await client.get(
                path + "/body/content", params={"reference": json.dumps(reference)}
            )
            assert stale.status_code == 409
            reference["version"] = original_version
            with db.connect(harness.runtime.database_path) as conn:
                conn.execute(
                    "DELETE FROM conversation_events WHERE conversation_id='c' AND sequence=?",
                    (row["order"]["sequence"],),
                )
                conn.execute(
                    "INSERT INTO conversation_expired_ranges VALUES ('c',?,?,1,2,1)",
                    (row["order"]["sequence"], row["order"]["sequence"]),
                )
            reference["version"] = "obsolete"
            for suffix in ("/body", "/body/content"):
                expired = await client.get(
                    path + suffix, params={"reference": json.dumps(reference)}
                )
                assert expired.status_code == 410
                assert expired.json()["error"]["code"] == "diagnostic_expired"
                malformed = dict(reference, body_id="invalid")
                assert (
                    await client.get(path + suffix, params={"reference": json.dumps(malformed)})
                ).status_code == 422
            with db.connect(harness.runtime.database_path) as conn:
                conn.execute("DELETE FROM conversation_view_rows WHERE conversation_id='c'")
            for suffix in ("/body", "/body/content"):
                assert (
                    await client.get(path + suffix, params={"reference": json.dumps(reference)})
                ).status_code == 410
            snapshot = (await client.get(path, params={"transcript": "false"})).json()
            assert snapshot["status"]["diagnostics_expired"] is True
            assert snapshot["page"]["projection_version"] == 1

    _run(exercise)


def test_a_warm_view_request_opens_no_database_connection(harness: _Harness) -> None:
    """Reads run on kept connections, and nothing may quietly start opening its own.

    This schema charges 2.39 ms for the first statement on a fresh connection against
    0.022 ms on a kept one, because the connection loads eighty tables before it can plan
    anything. Each of these requests used to pay that twice: once for its own read-only
    connection and once for the read-write one that answered "does this conversation
    exist" by reading the whole record.

    No existing test can see this. Every assertion about a view request passes whether it
    opened two connections or none, which is how it came to open two.
    """
    harness.app.include_router(router, prefix=CONVERSATION_PREFIX)
    opened: list[str] = []
    doors: dict[str, Any] = {"managed": db.connect, "sqlite": sqlite3.connect}

    def counting(name: str) -> Any:
        def open_one(*args: Any, **kwargs: Any) -> Any:
            opened.append(name)
            return doors[name](*args, **kwargs)

        return open_one

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                f"{CONVERSATION_PREFIX}/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "Owner asks"}],
                    "sender_label": "owner",
                    "sender_message_id": "receipt-one",
                },
            )
            path = f"{CONVERSATION_PREFIX}/conversations/c/view"
            # The pool opens its first connection on its first read, so warm it before
            # counting: this is about what a request costs, not what a boot costs.
            assert (await client.get(path)).status_code == 200

            db.connect = counting("managed")
            sqlite3.connect = counting("sqlite")
            try:
                assert (await client.get(path)).status_code == 200
                assert opened == [], f"a warm view page opened {opened}"
                receipts = await client.get(
                    path + "/receipts", params={"message_ids": json.dumps(["receipt-one"])}
                )
                assert receipts.status_code == 200
                assert opened == [], f"a warm view request opened {opened}"
            finally:
                db.connect, sqlite3.connect = doors["managed"], doors["sqlite"]

    _run(exercise)
