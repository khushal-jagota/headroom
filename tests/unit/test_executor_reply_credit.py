"""Recipient credit survives a stale source ending and belongs to exactly one turn."""

import asyncio
from pathlib import Path
from sqlite3 import Connection

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    ExplicitReplyMissingEventPayload,
    TurnEndedEventPayload,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.storage import ConversationStore
from planner.core.contracts import CHIEF_PRINCIPAL


def test_committed_reply_credit_survives_stale_ending_and_does_not_credit_next_turn(
    tmp_db: Connection, tmp_path: Path
) -> None:
    store = ConversationStore(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))

    async def run() -> None:
        await store.create_conversation(
            ResolvedConversationStart(
                "c_source",
                ConversationBackendKey.codex,
                "scripted",
                None,
                None,
                tmp_path,
                ConversationAccess.full,
            )
        )
        stale_ending = (
            ExplicitReplyMissingEventPayload(CHIEF_PRINCIPAL),
            TurnEndedEventPayload(ConversationTurnEnding.completed, None),
        )
        first = await store.reserve_turn("c_source")
        with tmp_db:
            store.credit_reply_in_transaction(tmp_db, TurnToken("c_source", first), CHIEF_PRINCIPAL)
        # A recipient commits after the source has prepared its ending from RAM.
        for turn_number, expected in (
            (first, ["turn_ended"]),
            (await store.reserve_turn("c_source"), ["explicit_reply_missing", "turn_ended"]),
        ):
            written = await store.append_turn_ending(
                "c_source",
                stale_ending,
                agent_activity=True,
                automatic_compaction_confirmed=False,
                automatic_compaction_result=None,
                lifecycle=(
                    LifecycleFact(
                        LifecycleKind.turn,
                        f"turn:{turn_number}",
                        turn_number,
                        LifecycleOperation.turn,
                        LifecycleStatus.unavailable,
                    ),
                ),
            )
            assert [row.kind.value for row in written] == expected
        assert tmp_db.execute("SELECT COUNT(*) FROM conversation_reply_credits").fetchone()[0] == 1

    with store.claim_runtime():
        asyncio.run(run())
