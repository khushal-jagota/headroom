from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest
from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    CancelNotification,
    DeniedOutcome,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptCapabilities,
    PromptRequest,
    PromptResponse,
    RequestPermissionRequest,
    RequestPermissionResponse,
    SessionInfoUpdate,
    SessionNotification,
    TextContentBlock,
    UserMessageChunk,
)
from acp.transports import default_environment

from planner.conversation.backend_contracts import (
    AcpEmployeeChildFactory,
    AgentBackendDefinition,
)
from planner.conversation.claude_backend import (
    CLAUDE_ACP_AGENT_VERSION,
    CLAUDE_BACKEND_KEY,
    CLAUDE_INHERITED_ENVIRONMENT_NAMES,
    ClaudeAcpEmployeeChildFactory,
    ClaudeBackendRequestMetadataError,
    ClaudeBackendStartupError,
    ClaudeBackendStartupPreflight,
    build_claude_acp_backend_definition,
    build_claude_employee_backend_registration,
)
from planner.conversation.claude_turn_strategy import ClaudeAcpTurnStrategy
from planner.conversation.contracts import (
    ContextCompaction,
    ConversationEmployee,
    ConversationSessionBinding,
)
from planner.conversation.sdk_child import build_confined_child_environment
from planner.conversation.wire_contracts import ProtocolUpdateRejectedPayload

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
NODE_EXECUTABLE = Path(shutil.which("node") or "").resolve()


def _employee() -> ConversationEmployee:
    return ConversationEmployee(
        employee_id="employee-claude",
        entity_kind="ticket",
        entity_id="ticket-claude",
        workspace_roots=(Path("/workspace/first"), Path("/workspace/second")),
        backend_key=CLAUDE_BACKEND_KEY,
    )


def _binding() -> ConversationSessionBinding:
    return ConversationSessionBinding(
        employee_id="employee-claude",
        acp_session_id="session-claude",
        backend_key=CLAUDE_BACKEND_KEY,
        binding_generation=4,
    )


def _definition(
    strategy: ClaudeAcpTurnStrategy | None = None,
) -> AgentBackendDefinition:
    return build_claude_acp_backend_definition(
        repository_root=REPOSITORY_ROOT,
        node_executable=NODE_EXECUTABLE,
        turn_strategy=strategy or ClaudeAcpTurnStrategy(),
    )


def _notification(text: str) -> SessionNotification:
    return SessionNotification(
        session_id="session-claude",
        update=AgentMessageChunk(
            session_update="agent_message_chunk",
            content=TextContentBlock(type="text", text=text),
        ),
    )


async def _deny_permission(
    _request: RequestPermissionRequest,
) -> RequestPermissionResponse:
    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


async def _ignore_death(_error: BaseException | None) -> None:
    return None


class _FakeChild:
    def __init__(self, generation: int, initialize_response: InitializeResponse) -> None:
        self.generation = generation
        self.alive = True
        self.supports_session_fork = False
        self.initialize_response = initialize_response
        self.initialize_requests: list[InitializeRequest] = []
        self.new_requests: list[NewSessionRequest] = []
        self.load_requests: list[LoadSessionRequest] = []
        self.capture_load_requests: list[LoadSessionRequest] = []
        self.prompt_requests: list[PromptRequest] = []
        self.closed = False
        self.force_closed = False

    async def initialize(self, request: InitializeRequest) -> InitializeResponse:
        self.initialize_requests.append(request)
        return self.initialize_response

    async def new_session(self, request: NewSessionRequest) -> NewSessionResponse:
        self.new_requests.append(request)
        return NewSessionResponse(session_id="session-claude")

    async def load_session(self, request: LoadSessionRequest) -> LoadSessionResponse:
        self.load_requests.append(request)
        return LoadSessionResponse()

    async def capture_load_session(
        self, request: LoadSessionRequest, private_ingress: Any
    ) -> LoadSessionResponse:
        del private_ingress
        self.capture_load_requests.append(request)
        return LoadSessionResponse()

    async def fork_session(self, request: Any) -> Any:
        raise AssertionError(f"Claude must not fork sessions: {request!r}")

    async def prompt(self, request: PromptRequest) -> PromptResponse:
        self.prompt_requests.append(request)
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, notification: CancelNotification) -> None:
        del notification

    async def close(self) -> None:
        self.closed = True
        self.alive = False

    async def force_close(self) -> None:
        self.force_closed = True
        self.alive = False


class _FakeFactory:
    def __init__(self, initialize_response: InitializeResponse) -> None:
        self.initialize_response = initialize_response
        self.children: list[_FakeChild] = []
        self.create_calls: list[tuple[ConversationEmployee, int]] = []
        self.update_ingress: Any = None

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _FakeChild:
        del permission_callback, death_callback
        self.create_calls.append((employee, generation))
        self.update_ingress = update_ingress
        child = _FakeChild(generation, self.initialize_response)
        self.children.append(child)
        return child


def _initialize_response(
    *,
    protocol_version: int = PROTOCOL_VERSION,
    agent_name: str = "@agentclientprotocol/claude-agent-acp",
    agent_version: str = CLAUDE_ACP_AGENT_VERSION,
    load_session: bool = True,
    image: bool = True,
    embedded_context: bool = True,
) -> InitializeResponse:
    return InitializeResponse(
        protocol_version=protocol_version,
        agent_capabilities=AgentCapabilities(
            load_session=load_session,
            prompt_capabilities=PromptCapabilities(
                image=image,
                embedded_context=embedded_context,
            ),
        ),
        agent_info=Implementation(
            name=agent_name,
            version=agent_version,
        ),
    )


def test_claude_definition_freezes_locked_runtime_and_truthful_capabilities() -> None:
    strategy = ClaudeAcpTurnStrategy()
    definition = _definition(strategy)

    assert definition.backend_key == CLAUDE_BACKEND_KEY == "claude"
    assert definition.argv == (
        str(NODE_EXECUTABLE),
        str(
            REPOSITORY_ROOT
            / "agent_backends/node_modules/@agentclientprotocol/claude-agent-acp/dist/index.js"
        ),
    )
    assert definition.expected_agent_name == "@agentclientprotocol/claude-agent-acp"
    assert definition.expected_agent_version == CLAUDE_ACP_AGENT_VERSION == "0.60.0"
    assert definition.inherited_environment_names == CLAUDE_INHERITED_ENVIRONMENT_NAMES
    assert definition.environment_overrides == ()
    assert definition.turn_capabilities.supports_steer is False
    assert definition.turn_capabilities.observes_compaction is True
    assert (
        definition.turn_capabilities.requires_fresh_child_after_requested_cancel
        is True
    )
    assert definition.reverse_service_capabilities.filesystem is False
    assert definition.reverse_service_capabilities.terminal is False
    assert definition.reverse_service_capabilities.permission is True
    assert definition.working_directory_for(_employee()) == Path("/workspace/first")
    assert definition.turn_strategy is strategy


def test_claude_environment_is_confined_and_keeps_only_panels_worker_identity() -> None:
    ambient = {
        **default_environment(),
        "ANTHROPIC_API_KEY": "secret",
        "CLAUDE_CODE_EXECUTABLE": "/global/claude",
        "CLAUDE_CONFIG_DIR": "/custom/config",
        "HERMES_HOME": "/ambient/hermes",
        "OPENAI_API_KEY": "secret",
        "PLAN_ACTOR": "stale",
        "PLAN_TICKET_ID": "stale",
    }
    environment = build_confined_child_environment(
        _definition(), _employee(), ambient_environment=ambient
    )

    assert environment["PLAN_ACTOR"] == "worker"
    assert environment["PLAN_TICKET_ID"] == "ticket-claude"
    assert set(environment) == {
        *default_environment(),
        "PLAN_ACTOR",
        "PLAN_TICKET_ID",
    }
    assert "ANTHROPIC_API_KEY" not in environment
    assert "CLAUDE_CODE_EXECUTABLE" not in environment
    assert "CLAUDE_CONFIG_DIR" not in environment
    assert "HERMES_HOME" not in environment
    assert "OPENAI_API_KEY" not in environment


def test_zero_arg_claude_registration_materializes_decorated_lazy_runtime(
    tmp_path: Path,
) -> None:
    from planner.conversation.backend_catalog import EmployeeBackendBuildContext

    registration = build_claude_employee_backend_registration()
    assert registration.backend_key == CLAUDE_BACKEND_KEY

    materialized = registration.runtime_builder(
        EmployeeBackendBuildContext(
            data_directory=tmp_path,
            repository_root=REPOSITORY_ROOT,
        )
    )
    assert materialized.definition.backend_key == CLAUDE_BACKEND_KEY
    assert isinstance(materialized.child_factory, ClaudeAcpEmployeeChildFactory)
    assert materialized.child_factory.definition is materialized.definition
    assert materialized.startup_preflight is not None
    assert materialized.is_executable() is False


@pytest.mark.parametrize(
    ("repository_root", "node_executable", "message"),
    [
        (Path("relative/repository"), NODE_EXECUTABLE, "repository_root must be absolute"),
        (REPOSITORY_ROOT, Path("relative/node"), "node_executable must be absolute"),
        (REPOSITORY_ROOT, Path("/missing/panels-node"), "Node executable is missing"),
    ],
)
def test_claude_definition_rejects_unresolved_runtime_paths(
    repository_root: Path, node_executable: Path, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        build_claude_acp_backend_definition(
            repository_root=repository_root,
            node_executable=node_executable,
            turn_strategy=ClaudeAcpTurnStrategy(),
        )


def test_claude_factory_decorates_new_and_load_without_mutating_caller_metadata() -> None:
    async def exercise() -> None:
        fake = _FakeFactory(_initialize_response())
        factory = ClaudeAcpEmployeeChildFactory(
            _definition(),
            repository_root=REPOSITORY_ROOT,
            delegate_factory=fake,
        )
        delivered: list[SessionNotification] = []

        async def ingress(
            notification: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            assert isinstance(notification, SessionNotification)
            delivered.append(notification)

        child = await factory.create(
            _employee(),
            2,
            ingress,
            _deny_permission,
            _ignore_death,
        )
        original_meta = {"trace": {"id": "keep"}}
        new_request = NewSessionRequest(
            cwd="/workspace/first",
            additional_directories=["/workspace/second"],
            mcp_servers=[],
            field_meta=original_meta,
        )
        load_request = LoadSessionRequest(
            cwd="/workspace/first",
            additional_directories=["/workspace/second"],
            mcp_servers=[],
            session_id="session-claude",
            field_meta=original_meta,
        )

        await child.new_session(new_request)
        await child.load_session(load_request)
        await child.capture_load_session(load_request, ingress)

        delegate = fake.children[0]
        for decorated in (
            delegate.new_requests[0],
            delegate.load_requests[0],
            delegate.capture_load_requests[0],
        ):
            assert decorated.field_meta is not original_meta
            assert decorated.field_meta is not None
            assert decorated.field_meta["trace"] == {"id": "keep"}
            assert decorated.field_meta["systemPrompt"] == {
                "type": "preset",
                "preset": "claude_code",
                "append": factory.worker_system_prompt_append,
            }
            assert str(REPOSITORY_ROOT / "skills/panels-worker/SKILL.md") in (
                factory.worker_system_prompt_append
            )
            assert str(REPOSITORY_ROOT / "skills") in factory.worker_system_prompt_append
        assert original_meta == {"trace": {"id": "keep"}}

        conflicting = new_request.model_copy(
            update={"field_meta": {"systemPrompt": "caller-owned"}}
        )
        with pytest.raises(ClaudeBackendRequestMetadataError, match="systemPrompt"):
            await child.new_session(conflicting)

    asyncio.run(exercise())


def test_claude_factory_suppresses_only_exact_compaction_control_chunks() -> None:
    async def exercise() -> None:
        fake = _FakeFactory(_initialize_response())
        factory = ClaudeAcpEmployeeChildFactory(
            _definition(),
            repository_root=REPOSITORY_ROOT,
            delegate_factory=fake,
        )
        delivered: list[SessionNotification] = []

        async def ingress(
            notification: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            assert isinstance(notification, SessionNotification)
            delivered.append(notification)

        await factory.create(
            _employee(),
            1,
            ingress,
            _deny_permission,
            _ignore_death,
        )
        controls = (
            "Compacting...",
            "\n\nCompacting completed.",
            "\n\nCompacting failed: exact adapter reason",
            "\n\nCompacting failed.",
        )
        for text in controls:
            await fake.update_ingress(_notification(text))
        ordinary = _notification("Compacting... but this is ordinary text")
        await fake.update_ingress(ordinary)

        for text, notification in zip(controls, delivered[: len(controls)], strict=True):
            assert isinstance(notification.update, SessionInfoUpdate)
            assert notification.update.field_meta == {
                "claude": {"compactionControl": text}
            }
        assert delivered[-1] is ordinary

    asyncio.run(exercise())


def test_claude_compaction_state_is_exact_first_terminal_wins_and_reusable() -> None:
    async def exercise() -> None:
        strategy = ClaudeAcpTurnStrategy()
        binding = _binding()

        start = strategy.observe_compaction(binding, _control("Compacting..."))
        assert start is not None
        assert start.boundary_id == "employee-claude:session-claude:4:1"
        assert start.state == "compacting"
        assert start.trigger == "automatic"
        repeated = strategy.observe_compaction(binding, _control("Compacting..."))
        assert repeated == start
        assert strategy.observe_compaction(
            binding, _control("\n\nCompacting completed.")
        ) is None
        assert strategy.observe_compaction(
            binding, _control("\n\nCompacting failed: too late")
        ) is None

        completed = await strategy.capture_compaction_in_place(binding)
        assert completed.boundary_id == start.boundary_id
        assert completed.state == "compacted"
        assert completed.reason is None

        second = strategy.observe_compaction(binding, _control("Compacting..."))
        assert second is not None
        assert second.boundary_id == "employee-claude:session-claude:4:2"
        strategy.observe_compaction(
            binding, _control("\n\nCompacting failed: upstream exact failure")
        )
        failed = await strategy.capture_compaction_in_place(binding)
        assert failed.state == "failed"
        assert failed.reason == "Compacting failed: upstream exact failure"

    asyncio.run(exercise())


def test_claude_compaction_wait_is_in_place_and_generic_owner_supplies_deadline() -> None:
    async def exercise() -> None:
        strategy = ClaudeAcpTurnStrategy()
        binding = _binding()
        capture = asyncio.create_task(
            strategy.capture_compaction_in_place(binding)
        )
        await asyncio.sleep(0)
        assert capture.done() is False
        strategy.observe_compaction(binding, _control("Compacting..."))
        strategy.observe_compaction(binding, _control("\n\nCompacting completed."))
        result = await capture
        assert result.state == "compacted"

    asyncio.run(exercise())


def test_claude_compaction_wait_cancellation_resets_phase_but_retains_ordinal() -> None:
    async def exercise() -> None:
        strategy = ClaudeAcpTurnStrategy()
        binding = _binding()
        first = strategy.observe_compaction(binding, _control("Compacting..."))
        assert first is not None
        capture = asyncio.create_task(strategy.capture_compaction_in_place(binding))
        await asyncio.sleep(0)
        capture.cancel()
        with pytest.raises(asyncio.CancelledError):
            await capture

        second = strategy.observe_compaction(binding, _control("Compacting..."))
        assert second is not None
        assert second.boundary_id == "employee-claude:session-claude:4:2"

    asyncio.run(exercise())


def test_claude_steer_is_truthfully_rejected_and_replay_is_unchanged() -> None:
    async def exercise() -> None:
        strategy = ClaudeAcpTurnStrategy()
        receipt = await strategy.steer(
            _binding(),
            PromptRequest(
                session_id="session-claude",
                prompt=[TextContentBlock(type="text", text="steer")],
            ),
            "message-1",
        )
        assert receipt.model_dump() == {
            "client_message_id": "message-1",
            "choice": "steer",
            "state": "rejected",
            "queue_position": None,
            "reason": "Claude ACP does not support native steer",
        }
        ordinary = _notification("ordinary")
        assert strategy.classify_replay(_binding(), (ordinary,), ()) == (ordinary,)

    asyncio.run(exercise())


def test_claude_replay_replaces_private_compaction_summary_with_lifecycle() -> None:
    strategy = ClaudeAcpTurnStrategy()
    private_summary = SessionNotification(
        session_id="session-claude",
        update=UserMessageChunk(
            session_update="user_message_chunk",
            message_id="summary-message-id",
            content=TextContentBlock(
                type="text",
                text=(
                    "This session is being continued from a previous conversation "
                    "that ran out of context. The summary below covers the earlier "
                    "portion of the conversation.\n\nPrivate summary text.\n\n"
                    "If you need specific details from before compaction (like exact "
                    "code snippets, error messages, or content you generated), read "
                    "the full transcript at: /private/session.jsonl\nContinue the "
                    "conversation from where it left off without asking the user any "
                    "further questions. Resume directly — do not acknowledge the "
                    "summary, do not recap what was happening, do not preface with "
                    '"I\'ll continue" or similar. Pick up the last task as if the '
                    "break never happened."
                ),
            ),
        ),
    )
    ordinary = _notification("ordinary worker output")

    assert strategy.classify_replay(
        _binding(), (private_summary, ordinary), ()
    ) == (
        ContextCompaction(
            boundary_id=(
                "employee-claude:session-claude:4:claude-replay:summary-message-id"
            ),
            state="compacted",
            trigger="automatic",
        ),
        ordinary,
    )


def _control(text: str) -> SessionNotification:
    return SessionNotification(
        session_id="session-claude",
        update=SessionInfoUpdate(
            session_update="session_info_update",
            field_meta={"claude": {"compactionControl": text}},
        ),
    )


def test_claude_startup_preflight_is_initialize_only_and_closes_before_ready() -> None:
    async def exercise() -> None:
        fake = _FakeFactory(_initialize_response())
        factory = ClaudeAcpEmployeeChildFactory(
            _definition(),
            repository_root=REPOSITORY_ROOT,
            delegate_factory=fake,
        )
        preflight = ClaudeBackendStartupPreflight(
            definition=factory.definition,
            child_factory=factory,
            repository_root=REPOSITORY_ROOT,
            timeout_seconds=1,
        )
        assert preflight.is_executable() is False

        await preflight.run()

        assert preflight.is_executable() is True
        assert len(fake.create_calls) == 1
        employee, generation = fake.create_calls[0]
        assert employee.entity_kind == "ticket"
        assert employee.backend_key == CLAUDE_BACKEND_KEY
        assert employee.workspace_roots == (REPOSITORY_ROOT,)
        assert generation == 1
        child = fake.children[0]
        assert len(child.initialize_requests) == 1
        client_capabilities = child.initialize_requests[0].client_capabilities
        assert client_capabilities is not None
        assert client_capabilities.terminal is False
        assert client_capabilities.field_meta is None
        assert child.new_requests == []
        assert child.load_requests == []
        assert child.prompt_requests == []
        assert child.closed is True

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_initialize_response(protocol_version=2), "protocol version 1"),
        (_initialize_response(agent_name="wrong-agent"), "wrong agent identity"),
        (_initialize_response(agent_version="0.0.0"), "wrong agent identity"),
        (_initialize_response(load_session=False), "requires session/load"),
        (_initialize_response(image=False), "image prompt capability"),
        (
            _initialize_response(embedded_context=False),
            "embedded-context prompt capability",
        ),
    ],
)
def test_claude_startup_preflight_fails_closed_on_mismatch(
    response: InitializeResponse, message: str
) -> None:
    async def exercise() -> None:
        fake = _FakeFactory(response)
        factory = ClaudeAcpEmployeeChildFactory(
            _definition(),
            repository_root=REPOSITORY_ROOT,
            delegate_factory=fake,
        )
        preflight = ClaudeBackendStartupPreflight(
            definition=factory.definition,
            child_factory=factory,
            repository_root=REPOSITORY_ROOT,
            timeout_seconds=1,
        )
        with pytest.raises(ClaudeBackendStartupError, match=message):
            await preflight.run()
        assert preflight.is_executable() is False
        assert fake.children[0].closed is True

    asyncio.run(exercise())


class _HangingInitializeChild(_FakeChild):
    def __init__(self, generation: int, initialize_response: InitializeResponse) -> None:
        super().__init__(generation, initialize_response)
        self.initialize_started = asyncio.Event()
        self.initialize_task: asyncio.Task[InitializeResponse] | None = None
        self.initialize_cancelled = False

    async def initialize(self, request: InitializeRequest) -> InitializeResponse:
        self.initialize_requests.append(request)
        self.initialize_task = asyncio.current_task()
        self.initialize_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.initialize_cancelled = True
            raise
        raise AssertionError("unreachable")


class _HangingInitializeFactory(_FakeFactory):
    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _HangingInitializeChild:
        del permission_callback, death_callback
        self.create_calls.append((employee, generation))
        self.update_ingress = update_ingress
        child = _HangingInitializeChild(generation, self.initialize_response)
        self.children.append(child)
        return child


class _HangingInitializeAndCloseChild(_HangingInitializeChild):
    def __init__(self, generation: int, initialize_response: InitializeResponse) -> None:
        super().__init__(generation, initialize_response)
        self.close_task: asyncio.Task[None] | None = None
        self.close_cancelled = False

    async def close(self) -> None:
        self.close_task = asyncio.current_task()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.close_cancelled = True
            raise


class _HangingInitializeAndCloseFactory(_FakeFactory):
    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _HangingInitializeAndCloseChild:
        del permission_callback, death_callback
        self.create_calls.append((employee, generation))
        self.update_ingress = update_ingress
        child = _HangingInitializeAndCloseChild(generation, self.initialize_response)
        self.children.append(child)
        return child


def test_claude_startup_preflight_hang_uses_one_deadline_and_force_closes() -> None:
    async def exercise() -> None:
        fake = _HangingInitializeAndCloseFactory(_initialize_response())
        factory = ClaudeAcpEmployeeChildFactory(
            _definition(),
            repository_root=REPOSITORY_ROOT,
            delegate_factory=fake,
        )
        preflight = ClaudeBackendStartupPreflight(
            definition=factory.definition,
            child_factory=factory,
            repository_root=REPOSITORY_ROOT,
            timeout_seconds=0.01,
        )
        with pytest.raises(
            ClaudeBackendStartupError,
            match="timed out during initialize after 0.01 seconds",
        ):
            await preflight.run()
        assert preflight.is_executable() is False
        child = fake.children[0]
        assert isinstance(child, _HangingInitializeAndCloseChild)
        assert child.initialize_task is not None
        assert child.initialize_task.done()
        assert child.initialize_cancelled is True
        assert child.close_task is not None
        assert child.close_task.done()
        assert child.close_cancelled is True
        assert child.force_closed is True

    asyncio.run(exercise())


class _CancellationResistantSpawnFactory(_FakeFactory):
    def __init__(self, initialize_response: InitializeResponse) -> None:
        super().__init__(initialize_response)
        self.create_started = asyncio.Event()
        self.create_task: asyncio.Task[_FakeChild] | None = None

    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _FakeChild:
        del permission_callback, death_callback
        self.create_calls.append((employee, generation))
        self.update_ingress = update_ingress
        child = _FakeChild(generation, self.initialize_response)
        self.children.append(child)
        self.create_task = asyncio.current_task()
        self.create_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Model a factory which finishes its already-created child while
            # acknowledging cancellation. The preflight must own that result.
            return child
        raise AssertionError("unreachable")


def test_claude_startup_preflight_cancellation_during_spawn_settles_late_child() -> None:
    async def exercise() -> None:
        fake = _CancellationResistantSpawnFactory(_initialize_response())
        factory = ClaudeAcpEmployeeChildFactory(
            _definition(),
            repository_root=REPOSITORY_ROOT,
            delegate_factory=fake,
        )
        preflight = ClaudeBackendStartupPreflight(
            definition=factory.definition,
            child_factory=factory,
            repository_root=REPOSITORY_ROOT,
            timeout_seconds=1,
        )
        run = asyncio.create_task(preflight.run())
        await fake.create_started.wait()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert preflight.is_executable() is False
        assert fake.create_task is not None
        assert fake.create_task.done()
        assert fake.children[0].force_closed is True

    asyncio.run(exercise())


def test_claude_startup_preflight_cancellation_during_initialize_settles_child() -> None:
    async def exercise() -> None:
        fake = _HangingInitializeFactory(_initialize_response())
        factory = ClaudeAcpEmployeeChildFactory(
            _definition(),
            repository_root=REPOSITORY_ROOT,
            delegate_factory=fake,
        )
        preflight = ClaudeBackendStartupPreflight(
            definition=factory.definition,
            child_factory=factory,
            repository_root=REPOSITORY_ROOT,
            timeout_seconds=1,
        )
        run = asyncio.create_task(preflight.run())
        while not fake.children:
            await asyncio.sleep(0)
        child = fake.children[0]
        assert isinstance(child, _HangingInitializeChild)
        await child.initialize_started.wait()
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run

        assert preflight.is_executable() is False
        assert child.initialize_task is not None
        assert child.initialize_task.done()
        assert child.initialize_cancelled is True
        assert child.closed is True
        assert child.alive is False

    asyncio.run(exercise())


def test_exact_claude_package_initialize_preflight_uses_no_session_or_prompt() -> None:
    async def exercise() -> None:
        definition = _definition()
        factory = ClaudeAcpEmployeeChildFactory(
            definition,
            repository_root=REPOSITORY_ROOT,
        )
        preflight = ClaudeBackendStartupPreflight(
            definition=definition,
            child_factory=factory,
            repository_root=REPOSITORY_ROOT,
            timeout_seconds=30,
        )
        await preflight.run()
        assert preflight.is_executable() is True

    asyncio.run(exercise())


def test_factory_conforms_to_generic_child_factory_protocol() -> None:
    factory = ClaudeAcpEmployeeChildFactory(
        _definition(), repository_root=REPOSITORY_ROOT
    )
    generic_factory: AcpEmployeeChildFactory = factory
    assert generic_factory is factory
