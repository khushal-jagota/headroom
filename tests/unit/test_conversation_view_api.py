"""Selected-view HTTP join and demand boundaries over the existing real runtime harness."""

from __future__ import annotations

import json

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
            reference["version"] = "obsolete"
            stale = await client.get(
                path + "/body/content", params={"reference": json.dumps(reference)}
            )
            assert stale.status_code == 409

    _run(exercise)
