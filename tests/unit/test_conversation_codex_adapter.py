"""What the codex adapter has to be true about, against a codex that is scripted.

One obligation is left here, and it is the one no outside observer can see: a resume that
came back with somebody else's thread is refused rather than accepted in silence.
Downstream, a fresh thread and a restored one are indistinguishable — same shape, same
wire, an agent that answers — so a substitute taken quietly is the whole conversation's
memory gone with nothing to show for it. It cannot be provoked against a real codex, which
is why it is scripted here.

The bench it runs on — the scripted app-server, the recording sink, and the child's own
transcript of what it was sent — lives in
``tests/support/conversation_codex_app_server_bench.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.support.conversation_codex_app_server_bench import _run, _scripted_child

from planner.conversation.backends.contracts import (
    BackendSteerAccepted,
    BackendSteerRefused,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import PromptDeliveryRefusalReason
from planner.conversation.message_content import text_message_content


def test_a_resume_that_came_back_with_another_thread_is_refused(tmp_path: Path) -> None:
    """The documented codex trap: a resume that quietly hands back a different thread.

    Downstream, a fresh thread and a restored one look identical — same shape, same wire,
    an agent that answers. The only place the difference is visible is here, so this is
    where it has to be caught.
    """

    async def exercise() -> None:
        script = {"resume": {"outcome": "other_thread", "thread_id": "thread-somebody-else"}}
        async with _scripted_child(tmp_path, script=script) as scripted:
            with pytest.raises(SessionLoadFailed) as refused:
                await scripted.start(cursor="thread-earlier")
            assert "thread-somebody-else" in str(refused.value)

    _run(exercise)


def test_a_catalog_command_steered_into_a_running_turn_never_becomes_prose(
    tmp_path: Path,
) -> None:
    """Codex runs a command through its own RPC, and that RPC starts a new turn.

    So a command cannot join a turn that already runs. The one thing the adapter must
    never do is send it anyway: the model then reads ``/compact`` as a sentence and the
    command silently never runs. The adapter says it cannot take it, and the core holds
    the message for the turn after this one.
    """

    async def exercise() -> None:
        script = {"turns": [{"actions": []}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("run for a while"))

            command = text_message_content("/compact")
            outcome = await scripted.child.steer(
                TurnToken(conversation_id="c", turn_number=1),
                command,
                sender_content=command,
                sender_label="owner",
            )

            assert outcome == BackendSteerRefused(
                PromptDeliveryRefusalReason.command_cannot_join_running_turn
            )
            assert scripted.all_sent("turn/steer") == []

    _run(exercise)


def test_an_ordinary_steer_still_carries_its_sender_label(tmp_path: Path) -> None:
    """The guard is for commands alone. Ordinary text keeps the behaviour it has."""

    async def exercise() -> None:
        script = {"turns": [{"actions": []}]}
        async with _scripted_child(tmp_path, script=script) as scripted:
            await scripted.start(cursor=None)
            await scripted.write_prompt(1, text_message_content("run for a while"))

            spoken = text_message_content("stop and summarize")
            outcome = await scripted.child.steer(
                TurnToken(conversation_id="c", turn_number=1),
                spoken,
                sender_content=spoken,
                sender_label="owner",
            )

            assert outcome == BackendSteerAccepted(composed_content_delivered=True)
            steered = scripted.sent("turn/steer")
            assert "owner" in json.dumps(steered["params"]["input"])
            assert "stop and summarize" in json.dumps(steered["params"]["input"])

    _run(exercise)
