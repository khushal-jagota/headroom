"""Hosted Claude rebind uses the real adapter over the shared Node Mac/Python VPS.

Local adapter cleanup coverage cannot prove the live VPS retains the old assignment
and withholds a changed prompt until that exact SDK client has disconnected.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest
from tests.compare.direct.mac_end_to_end_fixture import boot, eventually

from planner.conversation.message_content import text_message_content
from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.execution_identity import ChildReference
from planner.executor.configuration import NATIVE_WORKER_CONTEXT
from planner.executor.protocol import type_adapter
from planner.runtime.conversation_start import send_to_ticket_conversation
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides

pytestmark = pytest.mark.usefixtures("executor_transport_home")


def test_claude_configuration_refusal_rebinds_only_after_exact_native_cleanup(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        async with boot(tmp_path, claude_cleanup=True) as harness:

            async def native() -> dict[str, Any]:
                state = await harness.state()
                value: dict[str, Any] = state["claude"]
                return value

            def prompt_rows(child_id: str) -> int:
                with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                    return int(
                        connection.execute(
                            "SELECT count(*) FROM executor_commands "
                            "WHERE operation='write_prompt' AND child_id=?",
                            (child_id,),
                        ).fetchone()[0]
                    )

            session = (await native())["clients"][0]["session_id"]
            sending = None
            with contextlib.closing(connect(harness.endpoint.db_path)) as connection:
                try:
                    for index, effort in enumerate(("low", "high"), 1):
                        ended = harness.events("turn_ended")
                        await harness.peer.call("claude_finish_turn")
                        await harness.wait_event("turn_ended", ended + 1)
                        await harness.wait_settled()
                        state = await harness.state()
                        old_reference = type_adapter(ChildReference).validate_python(
                            state["children"][0]["reference"]
                        )
                        old_prompt = (await native())["clients"][-1]["prompts"][-1]
                        old_rows = prompt_rows(old_reference.child_id)
                        await harness.peer.call("claude_hold_cleanup")
                        sender_text = f"Keep sender bytes {index}: café\nsecond line"
                        sending = asyncio.create_task(
                            send_to_ticket_conversation(
                                harness.system,
                                connection,
                                harness.ticket_id,
                                text_message_content(sender_text),
                                conversation_id="c_scripted",
                                sender_label="Chief",
                                sender=CHIEF_PRINCIPAL,
                                recipient=Principal(PrincipalKind.ticket, harness.ticket_id),
                                runs_under=ConversationStartOverrides(
                                    model="claude-new", reasoning_effort=effort
                                ),
                                now=3 + index,
                            )
                        )

                        async def cleanup_started() -> bool:
                            return bool((await native())["cleanup_entered"])

                        await eventually(cleanup_started, "Old Claude cleanup did not start")
                        held = await native()
                        assert not sending.done()
                        assert len(held["clients"]) == index
                        assert held["clients"][-1]["prompts"] == [old_prompt]
                        assert not held["clients"][-1]["disconnected"]
                        assert (
                            harness.endpoint.runs.store.child_assignment(old_reference) is not None
                        )
                        await harness.peer.call("claude_release_cleanup")
                        await asyncio.wait_for(sending, 5)

                        async def replaced(count: int = index + 1) -> bool:
                            clients = (await native())["clients"]
                            return len(clients) == count and len(clients[-1]["prompts"]) == 1

                        await eventually(replaced, "Replacement Claude did not receive its prompt")
                        await harness.wait_settled()
                        clients = (await native())["clients"]
                        replacement = clients[-1]
                        assert clients[-2]["disconnected"]
                        assert replacement["resume"] == session
                        assert (
                            replacement["model"] == "claude-new" and replacement["effort"] == effort
                        )
                        assert harness.endpoint.runs.store.child_assignment(old_reference) is None
                        assert prompt_rows(old_reference.child_id) == old_rows
                        prompt = replacement["prompts"][0]
                        assert prompt.startswith(NATIVE_WORKER_CONTEXT)
                        assert prompt.count(NATIVE_WORKER_CONTEXT) == prompt.count(sender_text) == 1
                        assert prompt.index("Chief:") < prompt.index(sender_text)
                    assert (
                        sum(len(client["prompts"]) for client in (await native())["clients"]) == 3
                    )
                finally:
                    await harness.peer.call("claude_release_cleanup")
                    if sending is not None and not sending.done():
                        sending.cancel()
                        await asyncio.gather(sending, return_exceptions=True)

    asyncio.run(run())
