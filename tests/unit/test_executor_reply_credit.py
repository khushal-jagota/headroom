"""A source snapshot prepared before recipient commit cannot erase durable credit."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection

from tests.unit.test_executor_native_runtime import canonical_snapshot

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
from planner.conversation.storage import ConversationStore
from planner.conversation.system import (
    SqliteProcessConversationSystem,
    _ConversationState,
    _RunningTurn,
)
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.execution_identity import ChildReference
from planner.executor.protocol import ChildCommand, CommandOutcome
from planner.executor.remote import RemoteBackendChild


def test_committed_reply_credit_survives_stale_metadata_and_turn_end(
    tmp_db: Connection, tmp_path: Path
) -> None:
    async def run() -> None:
        db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
        store = ConversationStore(db_path)
        record = await store.create_conversation(
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

        async def never_native(command: ChildCommand) -> CommandOutcome:
            raise AssertionError("race proof must not dispatch native work")

        state = _ConversationState(
            record=record,
            child=RemoteBackendChild(
                ChildReference("process", "child", "c_source"),
                never_native,
                lambda start: canonical_snapshot(),
            ),
        )
        running = _RunningTurn(TurnToken("c_source", 1), prompt_senders={CHIEF_PRINCIPAL: None})
        stale = SqliteProcessConversationSystem._executor_turn_metadata(state, running)
        ended = SqliteProcessConversationSystem._executor_turn_metadata(
            state, replace(running, ended=True)
        )
        assert stale is not None and ended is not None
        with tmp_db:
            store.save_executor_turn_in_transaction(tmp_db, "c_source", stale)
        # The source has already prepared stale RAM state, then another
        # conversation's canonical recipient append commits actual delivery credit.
        with tmp_db:
            store.credit_executor_reply_in_transaction(tmp_db, running.token, CHIEF_PRINCIPAL)
        with tmp_db:
            store.save_executor_turn_in_transaction(tmp_db, "c_source", stale)
        written = await store.append_turn_ending(
            "c_source",
            (
                ExplicitReplyMissingEventPayload(CHIEF_PRINCIPAL),
                TurnEndedEventPayload(ConversationTurnEnding.completed, None),
            ),
            agent_activity=True,
            automatic_compaction_confirmed=False,
            automatic_compaction_result=None,
            executor_turn=ended,
        )
        assert [event.kind.value for event in written] == ["turn_ended"]
        saved = await store.read_executor_turn("c_source")
        assert saved is not None
        assert json.loads(saved[2])["explicit_reply_recipients"] == [
            {"kind": "chief", "id": "chief"}
        ]
        # Credit belongs to one exact child and turn, never its replacement.
        next_turn = SqliteProcessConversationSystem._executor_turn_metadata(
            state, _RunningTurn(TurnToken("c_source", 2))
        )
        assert next_turn is not None
        with tmp_db:
            store.save_executor_turn_in_transaction(tmp_db, "c_source", next_turn)
        saved_next = await store.read_executor_turn("c_source")
        assert (
            saved_next is not None and json.loads(saved_next[2])["explicit_reply_recipients"] == []
        )

    asyncio.run(run())
