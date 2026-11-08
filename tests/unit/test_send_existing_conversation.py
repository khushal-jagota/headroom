"""A message into a conversation that exists is not a conversation start.

The iOS app names the active backend on every message and omits an unchanged model.
Startup settings come from launch defaults, which can differ from the conversation in
use, so only a real start or a real model choice may read them. These tests send through
``send_message`` against the in-memory conversation system, for the Chief and a Manager.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection
from typing import cast

import pytest
from starlette.requests import Request
from tests.support.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)

from planner.conversation.api import OwnerSendBody
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.contracts import PromptDeliveryQueued, PromptDeliveryStarted
from planner.conversation.message_files import ConversationMessageFiles
from planner.core.authctx import RequestContext
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.managers import data
from planner.managers.contracts import ManagerLaunchConfiguration
from planner.message_delivery.contracts import MessageDeliveryResult
from planner.message_delivery.service import send_message
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
from planner.tickets.api import send_to_chief_conversation
from planner.worker_settings.service import CHIEF_SETTINGS_KEY

CLAUDE_MODEL = "claude-opus-5-5"
OTHER_CLAUDE_MODEL = "claude-sonnet-5-5"
# What both owners launch on in a fresh database: a different backend from the ones below.
DEFAULT_BACKEND = Backend.codex
DEFAULT_MODEL = "gpt-6.1-sol"


@dataclass
class Scene:
    """One owner whose launch settings differ from the conversation it is in."""

    db: Connection
    system: InMemoryConversationSystem
    clock: FakeClock
    recipient: Principal
    agent_key: str
    conversation_id: str
    backend: Backend
    model: str
    launch_model: str

    async def send(
        self, runs_under: ConversationStartOverrides, **kwargs: object
    ) -> MessageDeliveryResult:
        return await send_message(
            self.system,
            self.db,
            self.clock,
            RequestContext(OWNER_PRINCIPAL),
            self.recipient,
            "Hello",
            runs_under=runs_under,
            **kwargs,  # type: ignore[arg-type]
        )

    def linked(self) -> str | None:
        return conversation_start.read_agent_conversation(self.db, self.agent_key)

    def model_changes(self) -> int:
        return sum(
            seen.kind is InMemoryConversationObservationKind.model_changed
            for seen in self.system.observations(self.conversation_id)
        )


async def _chief_scene(db: Connection, system: InMemoryConversationSystem) -> Scene:
    """A Chief on Claude, while its launch defaults say Codex. This is a valid start."""
    clock = FakeClock(datetime(2026, 11, 2, tzinfo=UTC))
    recipient = Principal(PrincipalKind.chief, "chief")
    started = await send_message(
        system,
        db,
        clock,
        RequestContext(OWNER_PRINCIPAL),
        recipient,
        "First",
        runs_under=ConversationStartOverrides(backend_key=Backend.claude, model=CLAUDE_MODEL),
    )
    assert started.conversation_id is not None
    return Scene(
        db, system, clock, recipient, CHIEF_SETTINGS_KEY, started.conversation_id,
        Backend.claude, CLAUDE_MODEL, DEFAULT_MODEL,
    )  # fmt: skip


async def _manager_scene(db: Connection, system: InMemoryConversationSystem) -> Scene:
    """A Manager in a Codex conversation, whose saved launch settings later say Claude."""
    clock = FakeClock(datetime(2026, 11, 2, tzinfo=UTC))
    item = data.create_manager(db, title="Existing", project_id="project_vylo", clock=clock)
    recipient = Principal(PrincipalKind.manager, item.id)
    started = await send_message(
        system, db, clock, RequestContext(OWNER_PRINCIPAL), recipient, "First"
    )
    assert started.conversation_id is not None
    data.update_manager_launch_configuration(
        db,
        item.id,
        ManagerLaunchConfiguration(Backend.claude, CLAUDE_MODEL, None),
        principal=OWNER_PRINCIPAL,
        clock=clock,
    )
    return Scene(
        db, system, clock, recipient, item.manager_agent_key, started.conversation_id,
        DEFAULT_BACKEND, DEFAULT_MODEL, CLAUDE_MODEL,
    )  # fmt: skip


SCENES: dict[str, Callable[[Connection, InMemoryConversationSystem], Awaitable[Scene]]] = {
    "chief": _chief_scene,
    "manager": _manager_scene,
}


@pytest.fixture(params=sorted(SCENES))
def owner(request: pytest.FixtureRequest, tmp_db: Connection, tmp_path: Path) -> str:
    return str(request.param)


@pytest.fixture(autouse=True)
def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(conversation_start, "_default_workspace_folder", lambda conn: tmp_path)


def _run(owner: str, db: Connection, body: Callable[[Scene], Awaitable[None]]) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await body(await SCENES[owner](db, system))

    asyncio.run(exercise())


def test_an_unchanged_send_reaches_the_conversation_it_is_in(
    owner: str, tmp_db: Connection
) -> None:
    """The reported payload, its Retry, and an omitted target all land in place."""

    async def body(scene: Scene) -> None:
        saved_before = (
            data.read_manager(tmp_db, scene.recipient.id).item
            if scene.recipient.kind is PrincipalKind.manager
            else None
        )
        restated = ConversationStartOverrides(backend_key=scene.backend)
        original = await scene.send(restated, sender_message_id="original")
        # Retry keeps the saved fields and mints a new identity.
        retry = await scene.send(restated, sender_message_id="retry")
        named = await scene.send(restated, conversation_id=scene.conversation_id)
        for sent in (original, retry, named):
            assert sent.conversation_id == scene.conversation_id
            assert isinstance(sent.fate, (PromptDeliveryStarted, PromptDeliveryQueued))
        assert scene.linked() == scene.conversation_id
        assert scene.model_changes() == 0
        assert scene.system.backend_model(scene.conversation_id) == scene.model
        if saved_before is not None:
            # Nothing inferred from the conversation overwrote the saved launch settings.
            saved_after = data.read_manager(tmp_db, scene.recipient.id).item
            assert saved_after.manager_launch_configuration == (
                saved_before.manager_launch_configuration
            )
            assert not saved_after.launch_configuration_is_explicit

    _run(owner, tmp_db, body)


def test_a_real_model_choice_still_changes_the_model(owner: str, tmp_db: Connection) -> None:
    async def body(scene: Scene) -> None:
        choice = ConversationStartOverrides(backend_key=scene.backend, model=OTHER_CLAUDE_MODEL)
        if scene.backend is not Backend.claude:
            choice = ConversationStartOverrides(backend_key=scene.backend, model="exact-custom")
        sent = await scene.send(choice)
        assert sent.conversation_id == scene.conversation_id
        assert isinstance(sent.fate, PromptDeliveryQueued)
        scene.system.complete_running_turn(scene.conversation_id)
        assert scene.system.backend_model(scene.conversation_id) == choice.model

    _run(owner, tmp_db, body)


def test_a_stale_target_fails_and_creates_nothing(owner: str, tmp_db: Connection) -> None:
    async def body(scene: Scene) -> None:
        restated = ConversationStartOverrides(backend_key=scene.backend)
        with pytest.raises(PlannerError) as stale:
            await scene.send(restated, conversation_id="c_stale")
        assert stale.value.code is ErrorCode.not_found
        assert scene.linked() == scene.conversation_id
        # With no linked conversation, a named one has nothing to be and nothing is made.
        await conversation_start.reset_agent_conversation(scene.system, tmp_db, scene.agent_key)
        with pytest.raises(PlannerError) as unlinked:
            await scene.send(restated, conversation_id=scene.conversation_id)
        assert unlinked.value.code is ErrorCode.not_found
        assert scene.linked() is None

    _run(owner, tmp_db, body)


def test_a_new_start_still_reads_launch_defaults_and_checks_its_backend(
    owner: str, tmp_db: Connection
) -> None:
    async def body(scene: Scene) -> None:
        await conversation_start.reset_agent_conversation(scene.system, tmp_db, scene.agent_key)
        # The active backend is not the launch backend for either owner.
        restated = ConversationStartOverrides(backend_key=scene.backend)
        with pytest.raises(PlannerError, match="different backend"):
            await scene.send(restated)
        assert scene.linked() is None
        started = await scene.send(ConversationStartOverrides())
        assert started.conversation_id is not None
        assert scene.system.backend_model(started.conversation_id) == scene.launch_model
        await conversation_start.reset_agent_conversation(scene.system, tmp_db, scene.agent_key)
        chosen = await scene.send(
            ConversationStartOverrides(backend_key=scene.backend, model=scene.model)
        )
        assert chosen.conversation_id is not None
        assert scene.system.backend_model(chosen.conversation_id) == scene.model

    _run(owner, tmp_db, body)


def test_a_manager_with_saved_intent_applies_it_to_a_restated_backend(tmp_db: Connection) -> None:
    """Explicit saved settings win over a restated backend, and an invalid one is refused."""

    async def body(scene: Scene) -> None:
        item_id = scene.recipient.id
        saved = ManagerLaunchConfiguration(DEFAULT_BACKEND, "exact-custom-model", None)
        data.update_manager_launch_configuration(
            tmp_db, item_id, saved,
            principal=OWNER_PRINCIPAL, clock=scene.clock, explicit_choice=True,
        )  # fmt: skip
        restated = ConversationStartOverrides(backend_key=DEFAULT_BACKEND)
        sent = await scene.send(restated)
        assert sent.conversation_id == scene.conversation_id
        scene.system.complete_running_turn(scene.conversation_id)
        assert scene.system.backend_model(scene.conversation_id) == "exact-custom-model"
        assert data.read_manager(tmp_db, item_id).item.manager_launch_configuration == saved
        # A legacy alias is a choice, and an invalid one is refused before anything is sent.
        with pytest.raises(PlannerError, match="model settings"):
            await scene.send(ConversationStartOverrides(backend_key=DEFAULT_BACKEND, model="sol"))
        assert data.read_manager(tmp_db, item_id).item.manager_launch_configuration == saved

    _run("manager", tmp_db, body)


def test_the_reported_payload_decodes_and_reaches_the_chief_over_the_http_route(
    tmp_db: Connection,
) -> None:
    async def body(scene: Scene) -> None:
        payload = OwnerSendBody.model_validate_json(
            '{"content":[{"piece":"text","text":"Hello"}],"sender_label":"Khushal",'
            f'"backend_key":"claude","conversation_id":"{scene.conversation_id}",'
            '"sender_message_id":"retry-1"}'
        )
        sent = await send_to_chief_conversation(
            payload,
            cast(Request, None),
            tmp_db,
            RequestContext(OWNER_PRINCIPAL),
            scene.clock,
            scene.system,
            cast(ConversationMessageFiles, None),
        )
        assert sent["conversation_id"] == scene.conversation_id
        assert scene.model_changes() == 0
        assert CHIEF_PRINCIPAL.kind is PrincipalKind.chief

    _run("chief", tmp_db, body)
