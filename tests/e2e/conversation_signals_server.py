"""Isolated browser proof: real conversation core, manually released backend turns.

Only the backend wire is scripted. Runtime, durable messages, projection, change
publication and owner-read use production code. No installed agent is invoked.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import Any

import uvicorn
from fastapi import FastAPI

from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendPromptAccepted,
    BackendSteerAccepted,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.events import (
    ConversationTurnEnding,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.snapshot import BackendSnapshotService, SubprocessBackendProbeEnvironment
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.server_lifecycle.application import ChangeStreamClosingServer


class ScriptedChild:
    def __init__(self, sink: BackendEventSink) -> None:
        self.sink = sink
        self.token: TurnToken | None = None
        self.answers: dict[str, tuple[UserInputAnswer, ...]] = {}

    async def start(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def write_prompt(
        self, turn_token: TurnToken, *args: Any, **kwargs: Any,
    ) -> BackendPromptAccepted:
        self.token = turn_token
        return BackendPromptAccepted(composed_content_delivered=True)

    async def steer(self, *args: Any, **kwargs: Any) -> BackendSteerAccepted:
        return BackendSteerAccepted()

    async def cancel_running_turn(self) -> None:
        self.token = None

    async def answer_permission_ask(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...],
    ) -> None:
        self.answers[request_id] = answers

    async def request_questions(self) -> None:
        assert self.token is not None
        await self.sink.user_input_requested(self.token, BackendUserInputRequest(
            request_id="input-1",
            questions=(
                UserInputQuestion(
                    question_id="scope", header="Scope",
                    question="Which surfaces should change?",
                    options=(
                        UserInputOption(label="Composer", description="The live composer"),
                        UserInputOption(label="Transcript", description="Durable history"),
                    ),
                    multi_select=True, allow_other=True,
                ),
                UserInputQuestion(
                    question_id="proof", header="Proof",
                    question="What proof should be required?",
                    options=(UserInputOption(
                        label="Browser test", description="Exercise the full flow",
                    ),),
                    multi_select=False, allow_other=True,
                ),
            ),
        ))
        await asyncio.sleep(0)

    async def stop(self) -> None:
        self.token = None

    async def finish(self) -> None:
        assert self.token is not None
        await self.sink.turn_ended(
            self.token, ending=ConversationTurnEnding.completed,
            error_summary=None, standard_error_tail=None,
        )
        self.token = None
        # The sink queues backend news. Yield so normal core processing can run.
        await asyncio.sleep(0)


class NoInstalledBackends(SubprocessBackendProbeEnvironment):
    """Composer metadata cannot probe or invoke any installed agent in this proof."""

    def executable_path(self, executable_name: str) -> str | None:
        return None

    def configured_executable_path(self, executable_name: str) -> str | None:
        return None


def main() -> None:
    config = load_config("config.yaml")
    assert config.test_mode
    assert config.db_path.endswith("conversation-signals-proof.db")
    with connect(config.db_path) as conn:
        create_schema(conn)
    children: dict[str, ScriptedChild] = {}

    def make_child(
        *, resolved_start: Any, event_sink: BackendEventSink, message_files: Any,
    ) -> ScriptedChild:
        child = ScriptedChild(event_sink)
        children[resolved_start.conversation_id] = child
        return child

    store = ConversationStore(config.db_path)
    tail = ConversationLiveTail()
    system = SqliteProcessConversationSystem(
        store=store,
        message_files=ConversationMessageFiles(config.db_path),
        backend_child_factories={key: make_child for key in ConversationBackendKey},
        live_tail=tail,
    )
    app = create_app(
        config, build_clock(config), lambda: connect(config.db_path),
        conversation_system_for_test=system,
    )

    configured_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def proof_lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with configured_lifespan(application):
            runtime = application.state.conversation
            # The normal test injection covers domain projection dependencies. Route
            # runtime/tail must use this same real core, rather than a second idle core.
            application.state.conversation = replace(
                runtime, system=system, live_tail=tail,
                backend_snapshots=BackendSnapshotService(NoInstalledBackends()),
            )
            try:
                yield
            finally:
                await system.shutdown()
                application.state.conversation = runtime

    app.router.lifespan_context = proof_lifespan

    @app.post("/api/test/signals/{conversation_id}/start")
    async def start(conversation_id: str) -> dict[str, bool]:
        if await store.read_conversation(conversation_id) is None:
            await system.start_conversation(ConversationStartRequest(
                conversation_id=conversation_id, model="scripted-proof",
            ))
        await system.send(
            conversation_id, text_message_content("Bounded proof turn"),
            sender_label="Owner", reply_requested=False,
        )
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/finish")
    async def finish(conversation_id: str) -> dict[str, bool]:
        await children[conversation_id].finish()
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/message")
    async def message(conversation_id: str, body: dict[str, str]) -> dict[str, bool]:
        await system.record_message_to_owner(
            conversation_id, text_message_content("Explicit message to owner"),
            sender_label=body["label"],
            sender=Principal(PrincipalKind(body["kind"]), body["id"]),
            recipient=OWNER_PRINCIPAL,
        )
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/queue")
    async def queue(conversation_id: str) -> dict[str, bool]:
        for number in range(1, 3):
            await system.send(
                conversation_id, text_message_content(f"queued message {number}"),
                sender_label="owner", sender_message_id=f"sender-{number}",
                reply_requested=False,
            )
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/questions")
    async def questions(conversation_id: str) -> dict[str, bool]:
        await children[conversation_id].request_questions()
        return {"ok": True}

    @app.get("/api/test/signals/{conversation_id}/answers")
    async def answers(conversation_id: str) -> dict[str, dict[str, dict[str, list[str]]]]:
        return {
            request_id: {
                answer.question_id: {"answers": list(answer.answers)} for answer in values
            }
            for request_id, values in children[conversation_id].answers.items()
        }

    ChangeStreamClosingServer(uvicorn.Config(app, host="127.0.0.1", port=config.port)).run()


if __name__ == "__main__":
    main()
