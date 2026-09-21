"""Behaviour that belongs to the in-memory conversation system alone.

The fake is not only a conformance subject: two supervisor seam tests reach the HTTP API
through it, and what they lean on it for is reply debt and idempotent replay. A message
sent twice under the same sender id is accepted once and reported fresh once, and a turn
that ends without the reply it was asked for leaves a marker saying so. If the fake drifted
on either, those tests would keep passing while describing something the real system does
not do — so the fidelity is pinned here, where the fake is the subject.
"""

from __future__ import annotations

import asyncio

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
)
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.conversation.message_content import text_message_content
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind


def test_in_memory_replays_report_freshness_and_silence_matches_production() -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await system.start_conversation(
            ConversationStartRequest(
                conversation_id="c",
                model="a-model",
                backend_key=ConversationBackendKey.hermes,
            )
        )
        recipient = Principal(PrincipalKind.ticket, "t_one")
        content = text_message_content("Please report")
        first = await system.send_with_receipt(
            "c",
            content,
            sender_label="owner",
            sender_message_id="m-1",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
        )
        replay = await system.send_with_receipt(
            "c",
            content,
            sender_label="owner",
            sender_message_id="m-1",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
            reply_requested=False,
        )
        assert isinstance(first.fate, PromptDeliveryStarted) and first.newly_accepted
        assert isinstance(replay.fate, PromptDeliveryStarted) and not replay.newly_accepted

        queued = await system.send_with_receipt(
            "c",
            text_message_content("next"),
            sender_label="owner",
            sender_message_id="m-2",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
        )
        queued_replay = await system.send_with_receipt(
            "c",
            text_message_content("next"),
            sender_label="owner",
            sender_message_id="m-2",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
            reply_requested=False,
        )
        assert isinstance(queued.fate, PromptDeliveryQueued) and queued.newly_accepted
        assert isinstance(queued_replay.fate, PromptDeliveryQueued)
        assert not queued_replay.newly_accepted

        system.complete_running_turn("c")
        markers = [
            observation.sender
            for observation in system.observations("c")
            if observation.kind is InMemoryConversationObservationKind.explicit_reply_missing
        ]
        assert markers == [OWNER_PRINCIPAL]

    asyncio.run(exercise())
