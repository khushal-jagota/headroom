from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from acp.schema import (
    CancelNotification,
    ForkSessionRequest,
    ForkSessionResponse,
    InitializeRequest,
    InitializeResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    SessionNotification,
    SetSessionConfigOptionResponse,
    TextContentBlock,
    UserMessageChunk,
)

from planner.conversation.contracts import ConversationEmployee
from planner.conversation.role_skill_kickoff import (
    RoleSkillKickoffAcpEmployeeChildFactory,
)


class _FakeChild:
    def __init__(self, generation: int, normal_ingress: Any) -> None:
        self.generation = generation
        self.alive = True
        self.supports_session_fork = True
        self.normal_ingress = normal_ingress
        self.new_responses: list[NewSessionResponse | BaseException] = [
            NewSessionResponse(session_id="session-new")
        ]
        self.new_requests: list[NewSessionRequest] = []
        self.load_requests: list[LoadSessionRequest] = []
        self.capture_load_requests: list[LoadSessionRequest] = []
        self.capture_private_ingress: Any = None
        self.fork_requests: list[ForkSessionRequest] = []
        self.prompt_requests: list[PromptRequest] = []
        self.load_updates: list[SessionNotification] = []
        self.capture_load_updates: list[SessionNotification] = []
        self.prompt_updates: list[SessionNotification] = []
        self.configuration_requests: list[tuple[str, str, str]] = []
        self.legacy_model_requests: list[tuple[str, str]] = []
        self.closed_session_ids: list[str] = []

    async def initialize(self, request: InitializeRequest) -> InitializeResponse:
        del request
        raise AssertionError("initialize is outside these focused tests")

    async def new_session(self, request: NewSessionRequest) -> NewSessionResponse:
        self.new_requests.append(request)
        response = self.new_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def set_config_option(
        self, session_id: str, config_id: str, value: str
    ) -> SetSessionConfigOptionResponse:
        self.configuration_requests.append((session_id, config_id, value))
        return SetSessionConfigOptionResponse(config_options=[])

    async def set_legacy_session_model(self, session_id: str, model_id: str) -> None:
        self.legacy_model_requests.append((session_id, model_id))

    async def close_session(self, session_id: str) -> None:
        self.closed_session_ids.append(session_id)

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        self.load_requests.append(request)
        for update in self.load_updates:
            await self.normal_ingress(update)
        return LoadSessionResponse()

    async def capture_load_session(
        self, request: LoadSessionRequest, private_ingress: Any
    ) -> LoadSessionResponse:
        self.capture_private_ingress = private_ingress
        self.capture_load_requests.append(request)
        for update in self.capture_load_updates:
            await private_ingress(update)
        return LoadSessionResponse()

    async def fork_session(self, request: ForkSessionRequest) -> ForkSessionResponse:
        self.fork_requests.append(request)
        return ForkSessionResponse(session_id="session-fork")

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self.prompt_requests.append(request)
        for update in self.prompt_updates:
            await self.normal_ingress(update)
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, notification: CancelNotification) -> None:
        del notification

    async def close(self) -> None:
        self.alive = False

    async def force_close(self) -> None:
        self.alive = False


class _FakeFactory:
    def __init__(self) -> None:
        self.children: list[_FakeChild] = []
        self.update_ingress: Any = None

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _FakeChild:
        del employee, permission_callback, death_callback
        self.update_ingress = update_ingress
        child = _FakeChild(generation, update_ingress)
        self.children.append(child)
        return child


def _employee(entity_kind: str = "ticket") -> ConversationEmployee:
    entity_id = "ticket-1" if entity_kind == "ticket" else "agent_panels_chief_of_staff"
    return ConversationEmployee(
        employee_id=entity_id,
        entity_kind=entity_kind,
        entity_id=entity_id,
        workspace_roots=(Path("/workspace"),),
        backend_key="probe",
    )


def _new_request() -> NewSessionRequest:
    return NewSessionRequest(cwd="/workspace", mcp_servers=[])


def _prompt(session_id: str, text: str) -> PromptRequest:
    return PromptRequest(
        session_id=session_id,
        prompt=[TextContentBlock(type="text", text=text)],
        field_meta={"trace": "preserved"},
    )


def _user_chunk(session_id: str, text: str) -> SessionNotification:
    return SessionNotification(
        session_id=session_id,
        update=UserMessageChunk(
            session_update="user_message_chunk",
            content=TextContentBlock(type="text", text=text),
        ),
    )


def test_ticket_new_session_decorates_only_its_first_prompt_delivery() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        factory = RoleSkillKickoffAcpEmployeeChildFactory(delegate)
        child = await factory.create(_employee(), 1, None, None, None)
        await child.new_session(_new_request())
        original_first = _prompt("session-new", "First real work")
        original_second = _prompt("session-new", "Continue")

        await child.prompt(original_first)
        await child.prompt(original_second)

        first, second = delegate.children[0].prompt_requests
        assert [block.text for block in first.prompt if isinstance(block, TextContentBlock)] == [
            "Use the installed `panels-worker` skill.",
            "First real work",
        ]
        assert first.field_meta == {"trace": "preserved"}
        assert second == original_second
        assert original_first.prompt == [TextContentBlock(type="text", text="First real work")]

    asyncio.run(exercise())


def test_role_skill_wrapper_forwards_configuration_model_and_session_close_unchanged() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        child = await RoleSkillKickoffAcpEmployeeChildFactory(delegate).create(
            _employee(), 1, None, None, None
        )

        response = await child.set_config_option("session-new", "model-id", "model-a")
        await child.set_legacy_session_model("session-new", "openrouter:provider-model")
        await child.close_session("session-new")

        assert response == SetSessionConfigOptionResponse(config_options=[])
        assert delegate.children[0].configuration_requests == [
            ("session-new", "model-id", "model-a")
        ]
        assert delegate.children[0].legacy_model_requests == [
            ("session-new", "openrouter:provider-model")
        ]
        assert delegate.children[0].closed_session_ids == ["session-new"]

    asyncio.run(exercise())


def test_chief_new_conversation_rearms_the_same_child_once() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        factory = RoleSkillKickoffAcpEmployeeChildFactory(delegate)
        child = await factory.create(_employee("agent"), 1, None, None, None)
        raw_child = delegate.children[0]
        raw_child.new_responses.append(NewSessionResponse(session_id="session-replacement"))

        await child.new_session(_new_request())
        await child.prompt(_prompt("session-new", "Plan this sprint"))
        await child.prompt(_prompt("session-new", "Continue planning"))
        await child.new_session(_new_request())
        await child.prompt(_prompt("session-replacement", "Start fresh"))

        assert [
            [block.text for block in request.prompt if isinstance(block, TextContentBlock)]
            for request in raw_child.prompt_requests
        ] == [
            [
                "Use the installed `panels-chief-of-staff` skill.",
                "Plan this sprint",
            ],
            ["Continue planning"],
            [
                "Use the installed `panels-chief-of-staff` skill.",
                "Start fresh",
            ],
        ]

    asyncio.run(exercise())


def test_failed_replacement_new_session_preserves_the_prior_role_arm() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        factory = RoleSkillKickoffAcpEmployeeChildFactory(delegate)
        child = await factory.create(_employee(), 1, None, None, None)
        raw_child = delegate.children[0]
        raw_child.new_responses.append(RuntimeError("replacement failed"))

        await child.new_session(_new_request())
        try:
            await child.new_session(_new_request())
        except RuntimeError as error:
            assert str(error) == "replacement failed"
        else:
            raise AssertionError("failed replacement session/new unexpectedly succeeded")
        await child.prompt(_prompt("session-new", "First real work"))

        assert [
            block.text
            for block in raw_child.prompt_requests[0].prompt
            if isinstance(block, TextContentBlock)
        ] == [
            "Use the installed `panels-worker` skill.",
            "First real work",
        ]

    asyncio.run(exercise())


def test_command_shaped_first_prompt_does_not_consume_the_role_arm() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        factory = RoleSkillKickoffAcpEmployeeChildFactory(delegate)
        child = await factory.create(_employee(), 1, None, None, None)
        await child.new_session(_new_request())
        command = _prompt("session-new", "/compact")
        ordinary = _prompt("session-new", "Start the Ticket")

        await child.prompt(command)
        await child.prompt(ordinary)

        delivered_command, delivered_ordinary = delegate.children[0].prompt_requests
        assert delivered_command == command
        assert [
            block.text for block in delivered_ordinary.prompt if isinstance(block, TextContentBlock)
        ] == [
            "Use the installed `panels-worker` skill.",
            "Start the Ticket",
        ]

    asyncio.run(exercise())


def test_failed_new_load_capture_and_fork_do_not_arm_role_kickoff() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        factory = RoleSkillKickoffAcpEmployeeChildFactory(delegate)
        child = await factory.create(_employee(), 2, None, None, None)
        raw_child = delegate.children[0]
        raw_child.new_responses = [RuntimeError("new failed")]
        load_request = LoadSessionRequest(
            cwd="/workspace",
            session_id="session-existing",
            mcp_servers=[],
        )

        try:
            await child.new_session(_new_request())
        except RuntimeError as error:
            assert str(error) == "new failed"
        else:
            raise AssertionError("failed session/new unexpectedly succeeded")
        await child.load_session(load_request)
        await child.capture_load_session(load_request, None)
        await child.fork_session(
            ForkSessionRequest(
                cwd="/workspace",
                session_id="session-existing",
                mcp_servers=[],
            )
        )
        await child.prompt(_prompt("session-existing", "Loaded continuation"))
        await child.prompt(_prompt("session-fork", "Fork continuation"))

        assert raw_child.load_requests == [load_request]
        assert raw_child.capture_load_requests == [load_request]
        assert [
            [block.text for block in request.prompt if isinstance(block, TextContentBlock)]
            for request in raw_child.prompt_requests
        ] == [["Loaded continuation"], ["Fork continuation"]]

    asyncio.run(exercise())


def test_live_role_echoes_are_visible_without_filtering_or_collision_suppression() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        factory = RoleSkillKickoffAcpEmployeeChildFactory(delegate)
        delivered: list[SessionNotification] = []

        async def ingress(notification: SessionNotification) -> None:
            delivered.append(notification)

        child = await factory.create(_employee(), 1, ingress, None, None)
        raw_child = delegate.children[0]
        directive = "Use the installed `panels-worker` skill."
        raw_child.prompt_updates = [
            _user_chunk("session-new", directive),
            _user_chunk("session-new", directive),
        ]

        await child.new_session(_new_request())
        await child.prompt(child.prompt_for_display(_prompt("session-new", directive)))
        await delegate.update_ingress(_user_chunk("session-new", directive))

        assert [
            notification.update.content.text
            for notification in delivered
            if isinstance(notification.update, UserMessageChunk)
            and isinstance(notification.update.content, TextContentBlock)
        ] == [directive, directive, directive]

    asyncio.run(exercise())


def test_load_and_capture_replay_forwards_role_echoes_unchanged() -> None:
    async def exercise() -> None:
        delegate = _FakeFactory()
        factory = RoleSkillKickoffAcpEmployeeChildFactory(delegate)
        delivered: list[SessionNotification] = []

        async def ingress(notification: SessionNotification) -> None:
            delivered.append(notification)

        child = await factory.create(_employee(), 2, ingress, None, None)
        raw_child = delegate.children[0]
        directive = "Use the installed `panels-worker` skill."
        load_request = LoadSessionRequest(
            cwd="/workspace",
            session_id="session-existing",
            mcp_servers=[],
        )
        raw_child.load_updates = [
            _user_chunk("different-session", directive),
            _user_chunk("session-existing", f"{directive}\nReplayed original"),
            _user_chunk("session-existing", directive),
        ]
        raw_child.capture_load_updates = [
            _user_chunk("session-existing", f"{directive}\n{directive}"),
        ]

        await child.load_session(load_request)
        await child.capture_load_session(load_request, ingress)
        await delegate.update_ingress(_user_chunk("session-existing", directive))

        assert [
            notification.update.content.text
            for notification in delivered
            if isinstance(notification.update, UserMessageChunk)
            and isinstance(notification.update.content, TextContentBlock)
        ] == [
            directive,
            f"{directive}\nReplayed original",
            directive,
            f"{directive}\n{directive}",
            directive,
        ]

    asyncio.run(exercise())
