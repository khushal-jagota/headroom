from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from acp.connection import StreamDirection, StreamEvent
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    CancelNotification,
    ForkSessionRequest,
    LoadSessionRequest,
    NewSessionRequest,
    PromptRequest,
    RequestPermissionResponse,
    SessionNotification,
    TextContentBlock,
)
from acp.stdio import spawn_agent_process
from acp.transports import default_environment
from tests.support.acp_reference_subject import ScriptedAcpClient

from planner.conversation import (
    AcpChildEnvironmentPolicyError,
    AcpChildError,
    AcpChildInitializeMismatch,
    AcpChildProcessExited,
    AcpConversationIngressFailure,
    AcpSessionUpdateCallbackMismatch,
    AcpSessionUpdateIngressClosed,
    AcpSessionUpdateIngressOverflow,
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ConversationEmployee,
    OrderedAcpConversationIngress,
    ProtocolUpdateRejectedPayload,
    ReverseServiceCapabilities,
    SdkAcpEmployeeChildFactory,
    build_confined_child_environment,
    build_panels_initialize_request,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_AGENT = REPOSITORY_ROOT / "tests" / "support" / "acp_scripted_agent.py"


class _TurnStrategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _employee(employee_id: str = "employee-1") -> ConversationEmployee:
    return ConversationEmployee(
        employee_id=employee_id,
        entity_kind="ticket",
        entity_id="ticket-17",
        workspace_roots=(REPOSITORY_ROOT, REPOSITORY_ROOT / "tests"),
        backend_key="scripted",
    )


def _definition(*, expected_name: str = "panels-scripted-agent") -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key="scripted",
        argv=(sys.executable, str(SCRIPTED_AGENT)),
        inherited_environment_names=tuple(default_environment()),
        environment_overrides=(),
        expected_agent_name=expected_name,
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(supports_steer=False, observes_compaction=False),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False, terminal=False, permission=True
        ),
        working_directory_resolver=lambda employee: employee.workspace_roots[1],
        turn_strategy=_TurnStrategy(),
    )


def _thought(text: str) -> SessionNotification:
    return SessionNotification(
        session_id="session-1",
        update=AgentThoughtChunk(
            session_update="agent_thought_chunk",
            content=TextContentBlock(type="text", text=text),
        ),
    )


def _raw_event(notification: SessionNotification) -> StreamEvent:
    return StreamEvent(
        StreamDirection.INCOMING,
        {
            "jsonrpc": "2.0",
            "method": "session/update",
            "params": notification.model_dump(
                mode="json", by_alias=True, exclude_none=True, exclude_unset=True
            ),
        },
    )


def test_ordered_ingress_reserves_raw_order_and_uses_typed_payloads() -> None:
    async def exercise() -> None:
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

        async def sink(item: SessionNotification | ProtocolUpdateRejectedPayload) -> None:
            received.append(item)

        ingress = OrderedAcpConversationIngress(sink, max_items=4)
        ingress.start()
        first, second = _thought("first"), _thought("second")
        ingress.observe_stream(_raw_event(first))
        ingress.observe_stream(_raw_event(second))
        ingress.fulfill_typed(second)
        ingress.fulfill_typed(first)
        await ingress.wait_until_consumed(2)
        assert received == [first, second]
        await ingress.close()

    asyncio.run(exercise())


def test_private_epoch_routes_matching_pre_request_update_by_exact_session_id() -> None:
    async def exercise() -> None:
        ordinary: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        private: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

        ingress = OrderedAcpConversationIngress(_append_async(ordinary))
        ingress.start()
        epoch = ingress.begin_response_consumption_epoch(
            "session/load",
            private_ingress=_append_async(private),
            private_session_id="candidate-session",
        )
        candidate = _thought("pre-request candidate").model_copy(
            update={"session_id": "candidate-session"}
        )

        ingress.observe_stream(_raw_event(candidate))
        ingress.fulfill_typed(candidate)
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 41, "method": "session/load", "params": {}},
            )
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "id": 41, "result": {}},
            )
        )

        await asyncio.wait_for(ingress.finish_response_consumption_epoch(epoch), timeout=1)
        assert ordinary == []
        assert private == [candidate]
        assert ingress.fatal_error is None
        await ingress.close()

    asyncio.run(exercise())


def test_private_epoch_keeps_other_session_public_and_waits_for_observed_prefix() -> None:
    async def exercise() -> None:
        ordinary: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        private: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        ordinary_entered = asyncio.Event()
        release_ordinary = asyncio.Event()

        async def ordinary_sink(
            item: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            ordinary_entered.set()
            await release_ordinary.wait()
            ordinary.append(item)

        ingress = OrderedAcpConversationIngress(ordinary_sink)
        ingress.start()
        epoch = ingress.begin_response_consumption_epoch(
            "session/load",
            private_ingress=_append_async(private),
            private_session_id="candidate-session",
        )
        source = _thought("ordinary source").model_copy(update={"session_id": "source-session"})

        ingress.observe_stream(_raw_event(source))
        ingress.fulfill_typed(source)
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 42, "method": "session/load", "params": {}},
            )
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "id": 42, "result": {}},
            )
        )
        finish = asyncio.create_task(ingress.finish_response_consumption_epoch(epoch))
        await asyncio.wait_for(ordinary_entered.wait(), timeout=1)
        assert not finish.done()

        release_ordinary.set()
        await asyncio.wait_for(finish, timeout=1)
        assert ordinary == [source]
        assert private == []
        assert ingress.last_reserved_ordinal == ingress.last_consumed_ordinal == 1
        await ingress.close()

    asyncio.run(exercise())


def test_private_epoch_routes_malformed_session_identity_to_visible_rejection() -> None:
    async def exercise() -> None:
        ordinary: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        private: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        ingress = OrderedAcpConversationIngress(_append_async(ordinary))
        ingress.start()
        epoch = ingress.begin_response_consumption_epoch(
            "session/load",
            private_ingress=_append_async(private),
            private_session_id="candidate-session",
        )

        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": " ",
                        "update": {"sessionUpdate": "future_update"},
                    },
                },
            )
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 43, "method": "session/load", "params": {}},
            )
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "id": 43, "result": {}},
            )
        )

        await asyncio.wait_for(ingress.finish_response_consumption_epoch(epoch), timeout=1)
        assert private == []
        assert len(ordinary) == 1
        rejection = ordinary[0]
        assert isinstance(rejection, ProtocolUpdateRejectedPayload)
        assert rejection.rejected_session_update == "future_update"
        await ingress.close()

    asyncio.run(exercise())


def test_response_epoch_rejects_incomplete_private_routing_shape() -> None:
    async def exercise() -> None:
        ingress = OrderedAcpConversationIngress(_discard)
        ingress.start()
        with pytest.raises(ValueError, match="private session ID"):
            ingress.begin_response_consumption_epoch("session/load", private_ingress=_discard)
        with pytest.raises(ValueError, match="private ingress"):
            ingress.begin_response_consumption_epoch(
                "session/load", private_session_id="candidate-session"
            )
        with pytest.raises(ValueError, match="private session ID"):
            ingress.begin_response_consumption_epoch(
                "session/load",
                private_ingress=_discard,
                private_session_id=" ",
            )
        await ingress.close()

    asyncio.run(exercise())


def test_hermes_shaped_fixture_is_exact_typed_order_without_thought_flattening() -> None:
    raw = json.loads(
        (REPOSITORY_ROOT / "tests/fixtures/acp/hermes-shaped-replay-v1.json").read_text()
    )
    notifications = tuple(SessionNotification.model_validate(item, strict=True) for item in raw)
    assert [item.update.session_update for item in notifications] == [
        "user_message_chunk",
        "agent_thought_chunk",
        "agent_message_chunk",
        "agent_thought_chunk",
        "agent_message_chunk",
        "tool_call",
        "tool_call_update",
        "plan",
        "plan",
        "available_commands_update",
        "usage_update",
        "session_info_update",
    ]
    thought_texts = {
        item.update.content.text
        for item in notifications
        if isinstance(item.update, AgentThoughtChunk)
        and isinstance(item.update.content, TextContentBlock)
    }
    assistant_texts = {
        item.update.content.text
        for item in notifications
        if isinstance(item.update, AgentMessageChunk)
        and isinstance(item.update.content, TextContentBlock)
    }
    assert thought_texts.isdisjoint(assistant_texts)


def test_invalid_observer_frame_consumes_visible_rejection_without_typed_callback() -> None:
    async def exercise() -> None:
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        ingress = OrderedAcpConversationIngress(received.append)  # type: ignore[arg-type]

        # Use an async callable while retaining a compact assertion sink.
        async def sink(item: SessionNotification | ProtocolUpdateRejectedPayload) -> None:
            received.append(item)

        ingress = OrderedAcpConversationIngress(sink)
        ingress.start()
        ingress.begin_load_epoch()
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 7, "method": "session/load", "params": {}},
            )
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {
                        "sessionId": "session-1",
                        "update": {"sessionUpdate": "future_update"},
                    },
                },
            )
        )
        ingress.observe_stream(
            StreamEvent(StreamDirection.INCOMING, {"jsonrpc": "2.0", "id": 7, "result": {}})
        )
        await ingress.finish_load_epoch()
        assert len(received) == 1
        assert isinstance(received[0], ProtocolUpdateRejectedPayload)
        assert received[0].rejected_session_update == "future_update"
        await ingress.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("params", "expected_discriminator"),
    [
        (
            {
                "session_id": "session-1",
                "update": {
                    "session_update": "agent_thought_chunk",
                    "content": {"type": "text", "text": "snake case"},
                },
            },
            "missing",
        ),
        (
            {
                "sessionId": "session-1",
                "update": {"sessionUpdate": "future\nupdate"},
            },
            "missing",
        ),
        ({"sessionId": "session-1"}, "missing"),
        (
            {
                "sessionId": "session-1",
                "update": {"sessionUpdate": "agent_thought_chunk"},
            },
            "agent_thought_chunk",
        ),
        (
            {
                "sessionId": "session-1",
                "update": {"sessionUpdate": "future_update"},
            },
            "future_update",
        ),
    ],
)
def test_malformed_load_reservation_is_transactional_alias_only_and_display_safe(
    params: dict[str, object], expected_discriminator: str
) -> None:
    async def exercise() -> None:
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []

        async def sink(item: SessionNotification | ProtocolUpdateRejectedPayload) -> None:
            received.append(item)

        ingress = OrderedAcpConversationIngress(sink)
        ingress.start()
        ingress.begin_load_epoch()
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 91, "method": "session/load", "params": {}},
            )
        )
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "method": "session/update", "params": params},
            )
        )
        assert ingress.last_reserved_ordinal == 1
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "id": 91, "result": {}},
            )
        )
        await asyncio.wait_for(ingress.finish_load_epoch(), timeout=1)
        assert len(received) == 1
        rejection = received[0]
        assert isinstance(rejection, ProtocolUpdateRejectedPayload)
        assert rejection.rejected_session_update == expected_discriminator
        await ingress.close()

    asyncio.run(exercise())


def test_sink_exception_is_generation_fatal_and_wakes_load_barrier() -> None:
    async def exercise() -> None:
        fatals: list[BaseException] = []

        async def failing_sink(item: object) -> None:
            del item
            raise ValueError("downstream failed")

        ingress = OrderedAcpConversationIngress(
            failing_sink,
            fatal_callback=fatals.append,  # type: ignore[arg-type]
        )
        ingress.start()
        ingress.begin_load_epoch()
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 8, "method": "session/load", "params": {}},
            )
        )
        notification = _thought("fails-in-sink")
        ingress.observe_stream(_raw_event(notification))
        ingress.fulfill_typed(notification)
        ingress.observe_stream(
            StreamEvent(StreamDirection.INCOMING, {"jsonrpc": "2.0", "id": 8, "result": {}})
        )
        with pytest.raises(AcpConversationIngressFailure):
            await asyncio.wait_for(ingress.finish_load_epoch(), timeout=1)
        assert isinstance(fatals[0], AcpConversationIngressFailure)
        assert isinstance(fatals[0].__cause__, ValueError)
        await ingress.close(drain=False)

    asyncio.run(exercise())


def test_overflow_is_fatal_but_drains_already_accepted_fulfilled_prefix() -> None:
    async def exercise() -> None:
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        fatals: list[BaseException] = []
        consumed = asyncio.Event()

        async def sink(item: SessionNotification | ProtocolUpdateRejectedPayload) -> None:
            received.append(item)
            consumed.set()

        ingress = OrderedAcpConversationIngress(sink, max_items=1, fatal_callback=fatals.append)
        ingress.start()
        accepted = _thought("accepted")
        ingress.observe_stream(_raw_event(accepted))
        ingress.observe_stream(_raw_event(_thought("overflow")))
        ingress.fulfill_typed(accepted)
        await asyncio.wait_for(consumed.wait(), timeout=1)
        assert received == [accepted]
        assert isinstance(fatals[0], AcpSessionUpdateIngressOverflow)
        await ingress.close(drain=False)

    asyncio.run(exercise())


def test_overflow_preserves_callbacks_delayed_until_the_next_event_loop_turn() -> None:
    async def exercise() -> None:
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        two_consumed = asyncio.Event()
        fatals: list[BaseException] = []

        async def sink(item: SessionNotification | ProtocolUpdateRejectedPayload) -> None:
            received.append(item)
            if len(received) == 2:
                two_consumed.set()

        ingress = OrderedAcpConversationIngress(sink, max_items=2, fatal_callback=fatals.append)
        ingress.start()
        ingress.begin_load_epoch()
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 93, "method": "session/load", "params": {}},
            )
        )
        first, second = _thought("accepted-a"), _thought("accepted-b")
        ingress.observe_stream(_raw_event(first))
        ingress.observe_stream(_raw_event(second))
        ingress.observe_stream(_raw_event(_thought("rejected-c")))
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "id": 93, "result": {}},
            )
        )
        with pytest.raises(AcpSessionUpdateIngressOverflow):
            await asyncio.wait_for(ingress.finish_load_epoch(), timeout=1)
        next_turn = asyncio.get_running_loop().create_future()
        asyncio.get_running_loop().call_soon(next_turn.set_result, None)
        await next_turn
        assert received == []
        ingress.fulfill_typed(first)
        ingress.fulfill_typed(second)
        await asyncio.wait_for(two_consumed.wait(), timeout=1)
        assert received == [first, second]
        assert isinstance(fatals[0], AcpSessionUpdateIngressOverflow)
        assert ingress.last_reserved_ordinal == 2
        await ingress.close(drain=False)

    asyncio.run(exercise())


def test_non_draining_retirement_fails_response_observed_load_before_cancelling_sink() -> None:
    async def exercise() -> None:
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()

        async def sink(item: object) -> None:
            del item
            sink_entered.set()
            await release_sink.wait()

        ingress = OrderedAcpConversationIngress(sink)  # type: ignore[arg-type]
        ingress.start()
        ingress.begin_load_epoch()
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.OUTGOING,
                {"jsonrpc": "2.0", "id": 92, "method": "session/load", "params": {}},
            )
        )
        notification = _thought("blocked")
        ingress.observe_stream(_raw_event(notification))
        ingress.fulfill_typed(notification)
        ingress.observe_stream(
            StreamEvent(
                StreamDirection.INCOMING,
                {"jsonrpc": "2.0", "id": 92, "result": {}},
            )
        )
        load_finish = asyncio.create_task(ingress.finish_load_epoch())
        await sink_entered.wait()
        terminal = RuntimeError("forced retirement")
        await ingress.close(drain=False, cause=terminal)
        with pytest.raises(RuntimeError, match="forced retirement"):
            await asyncio.wait_for(load_finish, timeout=1)

    asyncio.run(exercise())


def test_child_close_retires_unfulfilled_observer_reservation_without_hanging() -> None:
    async def exercise() -> None:
        definition = _definition()
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(),
            1,
            _discard,
            _deny_permission,
            _discard_death,
        )
        await child.initialize(build_panels_initialize_request(definition))
        child._ordered_ingress.observe_stream(_raw_event(_thought("never-dispatched")))  # noqa: SLF001
        await asyncio.wait_for(child.close(), timeout=5)
        assert not child.alive
        assert child._ordered_ingress.fatal_error is not None  # noqa: SLF001

    asyncio.run(exercise())


def test_child_force_close_cancels_pending_reservation_without_new_deadline() -> None:
    async def exercise() -> None:
        definition = _definition()
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(),
            1,
            _discard,
            _deny_permission,
            _discard_death,
        )
        await child.initialize(build_panels_initialize_request(definition))
        child._ordered_ingress.observe_stream(_raw_event(_thought("pending-force")))  # noqa: SLF001
        await asyncio.wait_for(child.force_close(), timeout=2)
        assert not child.alive

    asyncio.run(exercise())


def test_unmatched_typed_callback_is_generation_fatal() -> None:
    async def exercise() -> None:
        fatals: list[BaseException] = []

        async def sink(item: object) -> None:
            del item

        ingress = OrderedAcpConversationIngress(sink, fatal_callback=fatals.append)  # type: ignore[arg-type]
        ingress.start()
        ingress.fulfill_typed(_thought("unreserved"))
        assert isinstance(fatals[0], AcpSessionUpdateCallbackMismatch)
        await ingress.close(drain=False)

    asyncio.run(exercise())


def test_environment_is_explicit_and_employee_identity_overrides_pollution() -> None:
    employee = _employee()
    ambient = {
        **default_environment(),
        "OPENAI_API_KEY": "must-not-leak",
        "PLAN_TICKET_ID": "stale",
        "PLAN_ACTOR": "stale",
        "HERMES_HOME": "/ambient/hermes",
        "HERMES_TUI_SKILLS": "1",
    }
    environment = build_confined_child_environment(
        _definition(), employee, ambient_environment=ambient
    )
    assert environment["PLAN_TICKET_ID"] == "ticket-17"
    assert environment["PLAN_ACTOR"] == "worker"
    assert "OPENAI_API_KEY" not in environment
    assert "HERMES_HOME" not in environment
    assert "HERMES_TUI_SKILLS" not in environment


def test_environment_fails_before_spawn_for_undeclared_sdk_default() -> None:
    definition = _definition()
    missing = next(iter(default_environment()))
    definition = replace(
        definition,
        inherited_environment_names=tuple(
            name for name in default_environment() if name != missing
        ),
    )
    with pytest.raises(AcpChildEnvironmentPolicyError, match=missing):
        build_confined_child_environment(definition, _employee())


def test_official_sdk_child_delegates_and_load_waits_for_sink_consumption() -> None:
    async def exercise() -> None:
        definition = _definition()
        factory = SdkAcpEmployeeChildFactory(definition)
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        load_sink_entered = asyncio.Event()
        release_load_sink = asyncio.Event()
        block_sink = False
        death_causes: list[BaseException | None] = []

        async def sink(item: SessionNotification | ProtocolUpdateRejectedPayload) -> None:
            received.append(item)
            if block_sink:
                load_sink_entered.set()
                await release_load_sink.wait()

        async def permission(request: Any) -> RequestPermissionResponse:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=request.options[0].option_id)
            )

        async def death(cause: BaseException | None) -> None:
            death_causes.append(cause)

        child = await factory.create(_employee(), 1, sink, permission, death)
        await child.initialize(build_panels_initialize_request(definition))
        created = await child.new_session(
            NewSessionRequest(
                cwd=str(REPOSITORY_ROOT / "tests"),
                additional_directories=[str(REPOSITORY_ROOT)],
                mcp_servers=[],
            )
        )
        await child.prompt(
            PromptRequest(
                session_id=created.session_id,
                prompt=[TextContentBlock(type="text", text="populate")],
                field_meta={"script": "default"},
            )
        )
        block_sink = True
        load_task = asyncio.create_task(
            child.load_session(
                LoadSessionRequest(
                    cwd=str(REPOSITORY_ROOT / "tests"),
                    session_id=created.session_id,
                    additional_directories=[str(REPOSITORY_ROOT)],
                    mcp_servers=[],
                )
            )
        )
        await asyncio.wait_for(load_sink_entered.wait(), timeout=2)
        assert not load_task.done()
        release_load_sink.set()
        await asyncio.wait_for(load_task, timeout=3)
        await child.cancel(CancelNotification(session_id=created.session_id))
        assert any(
            isinstance(item, SessionNotification) and isinstance(item.update, AgentThoughtChunk)
            for item in received
        )
        await child.close()
        await child.close()
        assert death_causes == [None]

    asyncio.run(exercise())


def test_official_sdk_child_sets_config_options_and_closes_temporary_session() -> None:
    async def exercise() -> None:
        definition = _definition()
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _discard, _deny_permission, _discard_death
        )
        try:
            await child.initialize(build_panels_initialize_request(definition))
            created = await child.new_session(
                NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
            )
            assert created.config_options is not None
            model = next(option for option in created.config_options if option.category == "model")
            refreshed = await child.set_config_option(created.session_id, model.id, "probe-alt")
            assert any(
                option.category == "model" and option.current_value == "probe-alt"
                for option in refreshed.config_options
                if hasattr(option, "current_value")
            )
            await child.close_session(created.session_id)
        finally:
            await child.close()

    asyncio.run(exercise())


def test_official_sdk_child_sends_exact_legacy_model_request_before_prompt(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        audit_path = tmp_path / "legacy-model-audit.jsonl"
        definition = replace(
            _definition(),
            environment_overrides=(("ACP_TEST_LEGACY_MODEL_AUDIT_PATH", str(audit_path)),),
        )
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _discard, _deny_permission, _discard_death
        )
        try:
            await child.initialize(build_panels_initialize_request(definition))
            created = await child.new_session(
                NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
            )
            await child.set_legacy_session_model(created.session_id, "openrouter:provider/model")
            await child.prompt(
                PromptRequest(
                    session_id=created.session_id,
                    prompt=[TextContentBlock(type="text", text="configured work")],
                )
            )
        finally:
            await child.close()

        audit = [json.loads(line) for line in audit_path.read_text().splitlines()]
        assert [
            {key: value for key, value in item.items() if key != "processId"} for item in audit
        ] == [
            {"event": "new_session", "sessionId": created.session_id},
            {
                "event": "set_model",
                "sessionId": created.session_id,
                "modelId": "openrouter:provider/model",
            },
            {
                "event": "prompt",
                "sessionId": created.session_id,
                "modelId": "openrouter:provider/model",
            },
        ]

    asyncio.run(exercise())


def test_official_sdk_child_forks_exact_session_and_privately_loads_ordered_replay() -> None:
    async def exercise() -> None:
        definition = _definition()
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _discard, _deny_permission, _discard_death
        )
        await child.initialize(build_panels_initialize_request(definition))
        assert child.supports_session_fork
        created = await child.new_session(
            NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
        )
        await child.prompt(
            PromptRequest(
                session_id=created.session_id,
                prompt=[TextContentBlock(type="text", text="populate fork")],
                field_meta={"script": "default"},
            )
        )

        forked = await child.fork_session(
            ForkSessionRequest(
                session_id=created.session_id,
                cwd=str(REPOSITORY_ROOT / "tests"),
                additional_directories=[str(REPOSITORY_ROOT)],
                mcp_servers=[],
                field_meta={"requestMarker": "exact-fork"},
            )
        )
        assert forked.session_id != created.session_id
        assert forked.field_meta == {
            "scripted": {
                "sourceSessionId": created.session_id,
                "cwd": str(REPOSITORY_ROOT / "tests"),
                "additionalDirectories": [str(REPOSITORY_ROOT)],
                "mcpServerCount": 0,
                "requestMarker": "exact-fork",
            }
        }

        replay: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        sink_entered = asyncio.Event()
        release_sink = asyncio.Event()

        async def private_sink(
            item: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            replay.append(item)
            sink_entered.set()
            await release_sink.wait()

        load = asyncio.create_task(
            child.capture_load_session(
                LoadSessionRequest(
                    cwd=str(REPOSITORY_ROOT / "tests"),
                    session_id=forked.session_id,
                    additional_directories=[str(REPOSITORY_ROOT)],
                    mcp_servers=[],
                ),
                private_sink,
            )
        )
        await asyncio.wait_for(sink_entered.wait(), timeout=2)
        assert not load.done()
        release_sink.set()
        await asyncio.wait_for(load, timeout=2)
        assert replay
        assert all(
            isinstance(item, SessionNotification) and item.session_id == forked.session_id
            for item in replay
        )
        await child.close()

    asyncio.run(exercise())


def test_official_sdk_child_normalizes_live_and_private_load_after_wire_match() -> None:
    async def exercise() -> None:
        def mark(notification: SessionNotification) -> SessionNotification:
            return notification.model_copy(
                update={"field_meta": {**(notification.field_meta or {}), "normalized": True}}
            )

        definition = replace(_definition(), session_notification_normalizer=mark)
        ordinary: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        private: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _append_async(ordinary), _deny_permission, _discard_death
        )
        try:
            await child.initialize(build_panels_initialize_request(definition))
            created = await child.new_session(
                NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
            )
            await child.prompt(
                PromptRequest(
                    session_id=created.session_id,
                    prompt=[TextContentBlock(type="text", text="normalizer boundary")],
                    field_meta={"script": "default"},
                )
            )
            assert ordinary
            assert all(
                isinstance(item, SessionNotification)
                and item.field_meta == {"normalized": True}
                for item in ordinary
            )

            await child.capture_load_session(
                LoadSessionRequest(
                    cwd=str(REPOSITORY_ROOT),
                    session_id=created.session_id,
                    mcp_servers=[],
                ),
                _append_async(private),
            )
            assert private
            assert all(
                isinstance(item, SessionNotification)
                and item.field_meta == {"normalized": True}
                for item in private
            )
        finally:
            await child.close()

    asyncio.run(exercise())


def test_official_sdk_post_fork_updates_route_by_exact_session_without_deadlock() -> None:
    async def scenario() -> None:
        definition = replace(
            _definition(),
            environment_overrides=(
                ("ACP_TEST_POST_FORK_SOURCE_UPDATE", "1"),
                ("ACP_TEST_POST_FORK_CANDIDATE_UPDATE", "1"),
            ),
        )
        ordinary: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        private: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        source_update_entered = asyncio.Event()
        release_source_update = asyncio.Event()

        async def ordinary_sink(
            item: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            if (
                isinstance(item, SessionNotification)
                and isinstance(item.update, AvailableCommandsUpdate)
                and [command.name for command in item.update.available_commands]
                == ["source-after-fork"]
            ):
                source_update_entered.set()
                await release_source_update.wait()
            ordinary.append(item)

        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, ordinary_sink, _deny_permission, _discard_death
        )
        try:
            await child.initialize(build_panels_initialize_request(definition))
            created = await child.new_session(
                NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
            )
            await child.prompt(
                PromptRequest(
                    session_id=created.session_id,
                    prompt=[TextContentBlock(type="text", text="populate fork")],
                    field_meta={"script": "default"},
                )
            )
            ordinary.clear()
            wire_order: list[str] = []

            def observe_order(event: StreamEvent) -> None:
                if (
                    event.direction is StreamDirection.OUTGOING
                    and event.message.get("method") == "session/load"
                ):
                    wire_order.append("candidate-load-request")
                    return
                if (
                    event.direction is not StreamDirection.INCOMING
                    or event.message.get("method") != "session/update"
                ):
                    return
                params = event.message.get("params")
                update = params.get("update") if isinstance(params, dict) else None
                commands = update.get("availableCommands") if isinstance(update, dict) else None
                if isinstance(update, dict) and update.get("sessionUpdate") == "user_message_chunk":
                    wire_order.append("candidate-replay")
                if not isinstance(commands, list):
                    return
                command_names = [
                    command.get("name") for command in commands if isinstance(command, dict)
                ]
                if command_names == ["candidate-after-fork"]:
                    wire_order.append("candidate-update")
                elif command_names == ["source-after-fork"]:
                    wire_order.append("source-update")

            child._connection._conn.add_observer(observe_order)  # noqa: SLF001
            forked = await child.fork_session(
                ForkSessionRequest(
                    session_id=created.session_id,
                    cwd=str(REPOSITORY_ROOT),
                    mcp_servers=[],
                )
            )
            load = asyncio.create_task(
                child.capture_load_session(
                    LoadSessionRequest(
                        cwd=str(REPOSITORY_ROOT),
                        session_id=forked.session_id,
                        mcp_servers=[],
                    ),
                    _append_async(private),
                )
            )

            await asyncio.wait_for(source_update_entered.wait(), timeout=2)
            assert not load.done()
            release_source_update.set()
            await asyncio.wait_for(load, timeout=2)

            assert all(
                isinstance(item, SessionNotification) and item.session_id == created.session_id
                for item in ordinary
            )
            assert all(
                isinstance(item, SessionNotification) and item.session_id == forked.session_id
                for item in private
            )
            assert [
                command.name
                for item in ordinary
                if isinstance(item, SessionNotification)
                and isinstance(item.update, AvailableCommandsUpdate)
                for command in item.update.available_commands
            ] == ["source-after-fork"]
            private_command_names = [
                command.name
                for item in private
                if isinstance(item, SessionNotification)
                and isinstance(item.update, AvailableCommandsUpdate)
                for command in item.update.available_commands
            ]
            assert private_command_names[0] == "candidate-after-fork"
            assert "compact" in private_command_names
            assert wire_order.index("source-update") < wire_order.index("candidate-replay")
            assert child.alive
            assert child._ordered_ingress.fatal_error is None  # noqa: SLF001
            assert not child._ordered_ingress._response_epochs  # noqa: SLF001
            assert not child._ordered_ingress._slots  # noqa: SLF001
            assert (  # noqa: SLF001
                child._ordered_ingress.last_reserved_ordinal
                == child._ordered_ingress.last_consumed_ordinal
            )
        finally:
            release_source_update.set()
            await child.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=6))


def test_official_sdk_post_load_metadata_returns_to_ordinary_ingress() -> None:
    async def scenario() -> None:
        definition = replace(
            _definition(),
            environment_overrides=(("ACP_TEST_POST_LOAD_METADATA", "1"),),
        )
        ordinary: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        private: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        metadata_observed = asyncio.Event()

        async def ordinary_sink(
            item: SessionNotification | ProtocolUpdateRejectedPayload,
        ) -> None:
            ordinary.append(item)
            if isinstance(item, SessionNotification) and isinstance(
                item.update, AvailableCommandsUpdate
            ):
                metadata_observed.set()

        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, ordinary_sink, _deny_permission, _discard_death
        )
        try:
            await child.initialize(build_panels_initialize_request(definition))
            created = await child.new_session(
                NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
            )
            await child.capture_load_session(
                LoadSessionRequest(
                    cwd=str(REPOSITORY_ROOT),
                    session_id=created.session_id,
                    mcp_servers=[],
                ),
                _append_async(private),
            )
            await asyncio.wait_for(metadata_observed.wait(), timeout=2)

            assert private == []
            assert len(ordinary) == 1
            metadata = ordinary[0]
            assert isinstance(metadata, SessionNotification)
            assert metadata.session_id == created.session_id
            assert isinstance(metadata.update, AvailableCommandsUpdate)
            assert [command.name for command in metadata.update.available_commands] == [
                "post-load-metadata"
            ]
        finally:
            await child.close()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_missing_fork_capability_rejects_before_official_rpc() -> None:
    async def exercise() -> None:
        definition = replace(
            _definition(),
            environment_overrides=(("ACP_TEST_FORK_SESSION", "0"),),
        )
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _discard, _deny_permission, _discard_death
        )
        await child.initialize(build_panels_initialize_request(definition))
        assert not child.supports_session_fork
        with pytest.raises(AcpChildError, match="session/fork"):
            await child.fork_session(
                ForkSessionRequest(
                    session_id="never-sent",
                    cwd=str(REPOSITORY_ROOT),
                    mcp_servers=[],
                )
            )
        assert "fork never-sent" not in child.stderr_tail
        await child.close()

    asyncio.run(exercise())


def test_empty_official_fork_session_id_fails_closed() -> None:
    async def exercise() -> None:
        definition = replace(
            _definition(),
            environment_overrides=(("ACP_TEST_EMPTY_FORK_ID", "1"),),
        )
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _discard, _deny_permission, _discard_death
        )
        await child.initialize(build_panels_initialize_request(definition))
        with pytest.raises(AcpChildError, match="empty session ID"):
            await child.fork_session(
                ForkSessionRequest(
                    session_id="source",
                    cwd=str(REPOSITORY_ROOT),
                    mcp_servers=[],
                )
            )
        await child.close()

    asyncio.run(exercise())


def test_direct_reference_load_waits_for_its_ordered_consumer_barrier() -> None:
    async def exercise() -> None:
        client = ScriptedAcpClient()
        client.start_ingress()
        async with spawn_agent_process(
            client,
            sys.executable,
            str(SCRIPTED_AGENT),
            cwd=REPOSITORY_ROOT,
            observers=[client.observe_stream],
        ) as (connection, _process):
            await connection.initialize(protocol_version=1)
            created = await connection.new_session(cwd=str(REPOSITORY_ROOT))
            before = len(client.callback_records)
            await connection.prompt(
                session_id=created.session_id,
                prompt=[TextContentBlock(type="text", text="populate")],
                script="default",
            )
            await client.wait_for_callback_count(before + 9)
            entered = asyncio.Event()
            release = asyncio.Event()
            client.block_next_consumption(entered, release)
            load_task = asyncio.create_task(
                client.load_session(
                    connection,
                    cwd=str(REPOSITORY_ROOT),
                    session_id=created.session_id,
                )
            )
            await asyncio.wait_for(entered.wait(), timeout=2)
            await asyncio.wait_for(client.load_response_observed.wait(), timeout=2)
            assert not load_task.done()
            release.set()
            await asyncio.wait_for(load_task, timeout=2)
        await client.stop_ingress()

    asyncio.run(exercise())


@pytest.mark.parametrize("terminal_action", ["force_close", "process_death"])
def test_response_observed_blocked_load_is_settled_by_child_terminal_path(
    terminal_action: str,
) -> None:
    async def exercise() -> None:
        definition = _definition()
        received_count = 0
        live_consumed = asyncio.Event()
        replay_sink_entered = asyncio.Event()
        replay_release = asyncio.Event()
        block_replay = False
        deaths: list[BaseException | None] = []
        death_observed = asyncio.Event()

        async def sink(item: object) -> None:
            nonlocal received_count
            del item
            received_count += 1
            if received_count == 9:
                live_consumed.set()
            if block_replay:
                replay_sink_entered.set()
                await replay_release.wait()

        async def death(cause: BaseException | None) -> None:
            deaths.append(cause)
            death_observed.set()

        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(),
            1,
            sink,  # type: ignore[arg-type]
            _deny_permission,
            death,
        )
        await child.initialize(build_panels_initialize_request(definition))
        created = await child.new_session(
            NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
        )
        await child.prompt(
            PromptRequest(
                session_id=created.session_id,
                prompt=[TextContentBlock(type="text", text="populate")],
                field_meta={"script": "default"},
            )
        )
        await live_consumed.wait()
        block_replay = True
        load = asyncio.create_task(
            child.load_session(
                LoadSessionRequest(
                    cwd=str(REPOSITORY_ROOT),
                    session_id=created.session_id,
                    mcp_servers=[],
                )
            )
        )
        await replay_sink_entered.wait()
        await child._ordered_ingress.wait_for_load_response_observed()  # noqa: SLF001
        if terminal_action == "force_close":
            await asyncio.wait_for(child.force_close(), timeout=2)
            expected_error: type[BaseException] = AcpSessionUpdateIngressClosed
        else:
            child._process.kill()  # noqa: SLF001
            await asyncio.wait_for(death_observed.wait(), timeout=3)
            expected_error = AcpChildProcessExited
        with pytest.raises(expected_error):
            await asyncio.wait_for(load, timeout=1)
        assert not child.alive
        if terminal_action == "process_death":
            assert len(deaths) == 1
            assert isinstance(deaths[0], AcpChildProcessExited)

    asyncio.run(exercise())


def test_initialize_identity_mismatch_closes_candidate() -> None:
    async def exercise() -> None:
        definition = _definition(expected_name="another-agent")
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(),
            1,
            _discard,
            _deny_permission,
            _discard_death,
        )
        with pytest.raises(AcpChildInitializeMismatch):
            await child.initialize(build_panels_initialize_request(definition))
        assert not child.alive

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("environment_overrides", "message"),
    [
        ((("ACP_TEST_PROTOCOL_VERSION", "2"),), "protocol"),
        ((("ACP_TEST_AGENT_NAME", "wrong-agent"),), "agent"),
        ((("ACP_TEST_AGENT_VERSION", "9.9.9"),), "agent version"),
        ((("ACP_TEST_LOAD_SESSION", "0"),), "session/load"),
    ],
)
def test_initialize_protocol_identity_version_and_load_capability_fail_closed(
    environment_overrides: tuple[tuple[str, str], ...], message: str
) -> None:
    async def exercise() -> None:
        definition = replace(_definition(), environment_overrides=environment_overrides)
        deaths: list[BaseException | None] = []

        async def death(cause: BaseException | None) -> None:
            deaths.append(cause)

        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _discard, _deny_permission, death
        )
        with pytest.raises(AcpChildInitializeMismatch, match=message):
            await child.initialize(build_panels_initialize_request(definition))
        assert not child.alive
        assert deaths == [None]

    asyncio.run(exercise())


def test_malformed_load_replay_becomes_typed_rejection_and_barrier_completes() -> None:
    async def exercise() -> None:
        definition = replace(
            _definition(),
            environment_overrides=(("ACP_TEST_MALFORMED_LOAD", "future"),),
        )
        received: list[SessionNotification | ProtocolUpdateRejectedPayload] = []
        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _append_async(received), _deny_permission, _discard_death
        )
        await child.initialize(build_panels_initialize_request(definition))
        created = await child.new_session(
            NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
        )
        await asyncio.wait_for(
            child.load_session(
                LoadSessionRequest(
                    cwd=str(REPOSITORY_ROOT),
                    session_id=created.session_id,
                    mcp_servers=[],
                )
            ),
            timeout=2,
        )
        assert len(received) == 1
        assert isinstance(received[0], ProtocolUpdateRejectedPayload)
        assert received[0].rejected_session_update == "future_update"
        await child.close()

    asyncio.run(exercise())


def test_stderr_flood_is_continuously_drained_into_bounded_replacement_tail() -> None:
    async def exercise() -> None:
        definition = replace(
            _definition(),
            environment_overrides=(("ACP_TEST_STDERR_BYTES", "70000"),),
        )
        child = await SdkAcpEmployeeChildFactory(definition, stderr_tail_max_bytes=1024).create(
            _employee(), 1, _discard, _deny_permission, _discard_death
        )
        # Initialization cannot complete if the subprocess blocks on an undrained
        # stderr pipe; the scripted flood is emitted before its response.
        await asyncio.wait_for(
            child.initialize(build_panels_initialize_request(definition)), timeout=3
        )
        await child.close()
        assert 0 < len(child.stderr_tail.encode()) <= 1024

    asyncio.run(exercise())


def test_force_shutdown_during_stderr_flood_kills_child_without_stranding_initialize() -> None:
    async def exercise() -> None:
        definition = replace(
            _definition(),
            environment_overrides=(("ACP_TEST_STDERR_BYTES", "5000000"),),
        )
        child = await SdkAcpEmployeeChildFactory(definition, stderr_tail_max_bytes=1024).create(
            _employee(), 1, _discard, _deny_permission, _discard_death
        )
        initialize = asyncio.create_task(
            child.initialize(build_panels_initialize_request(definition))
        )
        await asyncio.wait_for(child.wait_for_stderr_data(), timeout=2)
        await asyncio.wait_for(child.force_close(), timeout=2)
        with pytest.raises((ConnectionError, asyncio.CancelledError)):
            await asyncio.wait_for(initialize, timeout=1)
        assert not child.alive
        assert 0 < len(child.stderr_tail.encode()) <= 1024

    asyncio.run(exercise())


def test_unexpected_process_death_settles_once_with_status_and_stderr_tail() -> None:
    async def exercise() -> None:
        definition = _definition()
        deaths: list[BaseException | None] = []
        death_observed = asyncio.Event()

        async def death(cause: BaseException | None) -> None:
            deaths.append(cause)
            death_observed.set()

        child = await SdkAcpEmployeeChildFactory(definition).create(
            _employee(), 1, _discard, _deny_permission, death
        )
        await child.initialize(build_panels_initialize_request(definition))
        created = await child.new_session(
            NewSessionRequest(cwd=str(REPOSITORY_ROOT), mcp_servers=[])
        )
        with pytest.raises((ConnectionError, asyncio.CancelledError)):
            await child.prompt(
                PromptRequest(
                    session_id=created.session_id,
                    prompt=[TextContentBlock(type="text", text="die")],
                    field_meta={"script": "die"},
                )
            )
        await asyncio.wait_for(death_observed.wait(), timeout=3)
        assert len(deaths) == 1
        assert isinstance(deaths[0], AcpChildProcessExited)
        assert deaths[0].return_code == 23
        assert "deterministic death" in deaths[0].stderr_tail
        assert not child.alive
        await child.close()
        assert len(deaths) == 1

    asyncio.run(exercise())


def _append_async(
    target: list[SessionNotification | ProtocolUpdateRejectedPayload],
) -> Any:
    async def append(item: SessionNotification | ProtocolUpdateRejectedPayload) -> None:
        target.append(item)

    return append


async def _discard(item: object) -> None:
    del item


async def _deny_permission(request: Any) -> RequestPermissionResponse:
    del request
    return RequestPermissionResponse(outcome={"outcome": "cancelled"})


async def _discard_death(cause: BaseException | None) -> None:
    del cause
