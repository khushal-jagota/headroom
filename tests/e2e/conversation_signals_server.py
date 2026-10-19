"""Isolated browser proof: real conversation core, manually released backend turns.

Only the backend wire is scripted. Runtime, durable messages, projection, change
publication and owner-read use production code. No installed agent is invoked.
"""

from __future__ import annotations

import asyncio
from contextlib import closing
from typing import Any
from unittest.mock import patch

import uvicorn
from fastapi import FastAPI
from tests.support.model_catalogue import seed_test_model_catalogue

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
from planner.conversation.message_content import text_message_content
from planner.conversation.snapshot import SubprocessBackendProbeEnvironment
from planner.core.clock import build_clock
from planner.core.config import Config, load_config
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


def build_app(config: Config) -> FastAPI:
    assert config.test_mode
    with closing(connect(config.db_path)) as conn:
        create_schema(conn)
        seed_test_model_catalogue(conn)
    children: dict[str, ScriptedChild] = {}

    def make_child(
        *,
        resolved_start: Any,
        event_sink: BackendEventSink,
        message_files: Any,
    ) -> ScriptedChild:
        child = ScriptedChild(event_sink)
        children[resolved_start.conversation_id] = child
        return child

    from tests.e2e.connected_controls import install_connected_controls

    # Override only probe input for this isolated process. Every runtime owner stays real.
    patch(
        "planner.conversation.snapshot.SubprocessBackendProbeEnvironment", NoInstalledBackends,
    ).start()
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(config.db_path),
        backend_child_factories_for_test={key: make_child for key in ConversationBackendKey},
    )
    install_connected_controls(app, config.db_path)

    @app.post("/api/test/signals/{conversation_id}/start")
    async def start(conversation_id: str) -> dict[str, bool]:
        system = app.state.conversation.system
        store = app.state.conversation.store
        if await store.read_conversation(conversation_id) is None:
            await system.start_conversation(
                ConversationStartRequest(
                    conversation_id=conversation_id,
                    model="gpt-6.1-sol",
                )
            )
        await system.send(
            conversation_id,
            text_message_content("Bounded proof turn"),
            sender_label="Owner",
            reply_requested=False,
        )
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/finish")
    async def finish(conversation_id: str) -> dict[str, bool]:
        await children[conversation_id].finish()
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/message")
    async def message(conversation_id: str, body: dict[str, str]) -> dict[str, bool]:
        await app.state.conversation.system.record_message_to_owner(
            conversation_id,
            text_message_content("Explicit message to owner"),
            sender_label=body["label"],
            sender=Principal(PrincipalKind(body["kind"]), body["id"]),
            recipient=OWNER_PRINCIPAL,
        )
        return {"ok": True}

    @app.post("/api/test/signals/{conversation_id}/queue")
    async def queue(conversation_id: str) -> dict[str, bool]:
        for number in range(1, 3):
            await app.state.conversation.system.send(
                conversation_id,
                text_message_content(f"queued message {number}"),
                sender_label="owner",
                sender_message_id=f"sender-{number}",
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
            request_id: {answer.question_id: {"answers": list(answer.answers)} for answer in values}
            for request_id, values in children[conversation_id].answers.items()
        }

    return app


def main() -> None:
    config = load_config("config.yaml")
    assert config.db_path.endswith("conversation-signals-proof.db")
    app = build_app(config)
    ChangeStreamClosingServer(uvicorn.Config(app, host="127.0.0.1", port=config.port)).run()


if __name__ == "__main__":
    main()
