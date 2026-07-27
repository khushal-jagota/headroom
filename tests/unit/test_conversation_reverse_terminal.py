from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from acp.schema import (
    CreateTerminalRequest,
    EnvVariable,
    KillTerminalRequest,
    NewSessionRequest,
    PromptRequest,
    ReleaseTerminalRequest,
    RequestPermissionResponse,
    TerminalOutputRequest,
    TextContentBlock,
    WaitForTerminalExitRequest,
)
from acp.transports import default_environment

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.contracts import (
    ConversationEmployee,
    ConversationSessionBinding,
    ConversationTerminalState,
)
from planner.conversation.reverse_services.terminal import (
    AcpReverseTerminalError,
    ScopedAcpTerminalService,
)
from planner.conversation.sdk_child import (
    AcpChildUnsupportedReverseService,
    SdkAcpEmployeeChildFactory,
    build_panels_initialize_request,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "acp" / "terminal_fixture.py"


def _employee(root: Path, employee_id: str = "employee-a") -> ConversationEmployee:
    return ConversationEmployee(
        employee_id=employee_id,
        entity_kind="ticket",
        entity_id="ticket-a",
        workspace_roots=(root,),
        backend_key="fake",
    )


def _binding(employee_id: str = "employee-a") -> ConversationSessionBinding:
    return ConversationSessionBinding(
        employee_id=employee_id,
        acp_session_id="session-a",
        backend_key="fake",
        binding_generation=1,
    )


class _Publisher:
    def __init__(self) -> None:
        self.states: list[ConversationTerminalState] = []

    async def publish_terminal_state(self, employee: Any, binding: Any, state: Any):
        assert employee.employee_id == binding.employee_id
        self.states.append(state)


def _service(
    root: Path,
    publisher: _Publisher,
    *,
    maximum: int = 1024,
    enabled: bool = True,
    cleanup_timeout_seconds: float = 1,
):
    counter = iter(("terminal-1", "terminal-2", "terminal-3"))

    async def resolve(employee: ConversationEmployee, generation: int):
        return _binding(employee.employee_id) if generation == 1 else None

    return ScopedAcpTerminalService(
        resolve,
        publisher,
        terminal_enabled=enabled,
        terminal_id_factory=lambda: next(counter),
        base_environment={"BASE": "base", "OVERRIDE": "old"},
        default_working_directory=lambda employee: employee.workspace_roots[0],
        output_max_bytes=maximum,
        cleanup_timeout_seconds=cleanup_timeout_seconds,
    )


async def _wait_for_output(
    service: ScopedAcpTerminalService, employee: ConversationEmployee, text: str
):
    async with asyncio.timeout(3):
        while True:
            output = await service.terminal_output(
                employee,
                1,
                TerminalOutputRequest(session_id="session-a", terminal_id="terminal-1"),
            )
            if text in output.output:
                return output
            await asyncio.sleep(0.01)


def test_create_uses_literal_argv_confined_cwd_and_injected_environment(tmp_path: Path) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        service = _service(tmp_path, publisher)
        response = await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "facts", "$(not-a-shell)"],
                cwd=str(tmp_path),
                env=[EnvVariable(name="OVERRIDE", value="new")],
            ),
        )
        waited = await service.wait_for_terminal_exit(
            _employee(tmp_path),
            1,
            WaitForTerminalExitRequest(session_id="session-a", terminal_id=response.terminal_id),
        )
        assert waited.exit_code == 0
        output = await service.terminal_output(
            _employee(tmp_path),
            1,
            TerminalOutputRequest(session_id="session-a", terminal_id=response.terminal_id),
        )
        assert output.output == f"$(not-a-shell)|{tmp_path}|base|new|absent"
        assert publisher.states[0].terminal_output.output == ""
        assert publisher.states[-1].terminal_output.exit_status is not None
        await service.release_terminal(
            _employee(tmp_path),
            1,
            ReleaseTerminalRequest(session_id="session-a", terminal_id=response.terminal_id),
        )
        assert publisher.states[-1].lifecycle == "released"
        await service.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("maximum", "requested", "expected"), [(8, None, "é-three"), (20, 5, "three"), (20, 0, "")]
)
def test_output_is_utf8_bounded_and_truncated_from_beginning(
    tmp_path: Path, maximum: int, requested: int | None, expected: str
) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        service = _service(tmp_path, publisher, maximum=maximum)
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "stream"],
                output_byte_limit=requested,
            ),
        )
        await service.wait_for_terminal_exit(
            _employee(tmp_path),
            1,
            WaitForTerminalExitRequest(session_id="session-a", terminal_id="terminal-1"),
        )
        output = await service.terminal_output(
            _employee(tmp_path),
            1,
            TerminalOutputRequest(session_id="session-a", terminal_id="terminal-1"),
        )
        assert output.output == expected
        assert output.truncated
        await service.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_kill_force_stops_sigterm_ignoring_process_without_release(tmp_path: Path) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        service = _service(tmp_path, publisher)
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "hold"],
            ),
        )
        await _wait_for_output(service, _employee(tmp_path), "ready")
        await service.kill_terminal(
            _employee(tmp_path),
            1,
            KillTerminalRequest(session_id="session-a", terminal_id="terminal-1"),
        )
        output = await service.terminal_output(
            _employee(tmp_path),
            1,
            TerminalOutputRequest(session_id="session-a", terminal_id="terminal-1"),
        )
        assert output.exit_status is not None
        assert output.exit_status.signal == "SIGKILL"
        assert publisher.states[-1].lifecycle == "active"
        await service.release_terminal(
            _employee(tmp_path),
            1,
            ReleaseTerminalRequest(session_id="session-a", terminal_id="terminal-1"),
        )
        await service.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_cross_session_unknown_and_disabled_calls_fail_closed(tmp_path: Path) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        disabled = _service(tmp_path, publisher, enabled=False)
        with pytest.raises(AcpReverseTerminalError):
            await disabled.create_terminal(
                _employee(tmp_path),
                1,
                CreateTerminalRequest(session_id="session-a", command=sys.executable),
            )
        service = _service(tmp_path, publisher)
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "stream"],
            ),
        )
        with pytest.raises(AcpReverseTerminalError):
            await service.terminal_output(
                _employee(tmp_path),
                1,
                TerminalOutputRequest(session_id="other", terminal_id="terminal-1"),
            )
        await service.shutdown(asyncio.get_running_loop().time() + 2)

    asyncio.run(exercise())


def test_invalid_utf8_is_replaced_and_duplicate_release_is_idempotent(tmp_path: Path) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        service = _service(tmp_path, publisher)
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "invalid"],
            ),
        )
        await service.wait_for_terminal_exit(
            _employee(tmp_path),
            1,
            WaitForTerminalExitRequest(session_id="session-a", terminal_id="terminal-1"),
        )
        output = await service.terminal_output(
            _employee(tmp_path),
            1,
            TerminalOutputRequest(session_id="session-a", terminal_id="terminal-1"),
        )
        assert output.output == "a�b"
        request = ReleaseTerminalRequest(session_id="session-a", terminal_id="terminal-1")
        await asyncio.gather(
            service.release_terminal(_employee(tmp_path), 1, request),
            service.release_terminal(_employee(tmp_path), 1, request),
        )
        await service.release_terminal(_employee(tmp_path), 1, request)
        assert sum(state.lifecycle == "released" for state in publisher.states) == 1
        await service.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_release_force_kills_held_process_and_cleanup_replay_scope(tmp_path: Path) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        service = _service(tmp_path, publisher)
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "hold"],
            ),
        )
        await _wait_for_output(service, _employee(tmp_path), "ready")
        unfinished = await service.cleanup_child_generation(
            "employee-a",
            "session-a",
            99,
            asyncio.get_running_loop().time() + 1,
        )
        assert unfinished == ()
        assert service.display_snapshots(_binding())[-1].lifecycle == "active"
        unfinished = await service.cleanup_child_generation(
            "employee-a",
            "session-a",
            1,
            asyncio.get_running_loop().time() + 2,
        )
        assert unfinished == ()
        snapshots = service.display_snapshots(_binding())
        assert len(snapshots) == 1
        assert snapshots[0].lifecycle == "released"
        assert snapshots[0].terminal_output.exit_status is not None
        assert snapshots[0].terminal_output.exit_status.signal == "SIGKILL"
        unfinished = await service.prepare_new_conversation(
            _binding(), asyncio.get_running_loop().time() + 1
        )
        assert unfinished == ()
        assert service.display_snapshots(_binding()) == ()
        await service.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_bad_cwd_environment_and_cross_employee_terminal_id_are_rejected(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        publisher = _Publisher()
        service = _service(root, publisher)
        for request in (
            CreateTerminalRequest(session_id="session-a", command=sys.executable, cwd=str(outside)),
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                env=[EnvVariable(name="BAD=NAME", value="x")],
            ),
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                env=[EnvVariable(name="GOOD", value="bad\x00value")],
            ),
        ):
            with pytest.raises(AcpReverseTerminalError):
                await service.create_terminal(_employee(root), 1, request)
        await service.create_terminal(
            _employee(root),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "stream"],
            ),
        )
        with pytest.raises(AcpReverseTerminalError):
            await service.terminal_output(
                _employee(root, "employee-b"),
                1,
                TerminalOutputRequest(session_id="session-a", terminal_id="terminal-1"),
            )
        await service.shutdown(asyncio.get_running_loop().time() + 2)

    asyncio.run(exercise())


def test_publisher_failure_wakes_waiters_and_concurrent_shutdown_is_bounded(
    tmp_path: Path,
) -> None:
    class FailingPublisher(_Publisher):
        async def publish_terminal_state(self, employee: Any, binding: Any, state: Any) -> None:
            if self.states:
                raise RuntimeError("terminal publisher failed")
            await super().publish_terminal_state(employee, binding, state)

    async def exercise() -> None:
        publisher = FailingPublisher()
        service = _service(tmp_path, publisher)
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "stream"],
            ),
        )
        with pytest.raises(AcpReverseTerminalError, match="publisher failed"):
            await service.wait_for_terminal_exit(
                _employee(tmp_path),
                1,
                WaitForTerminalExitRequest(session_id="session-a", terminal_id="terminal-1"),
            )
        deadline = asyncio.get_running_loop().time() + 1
        first, second = await asyncio.gather(service.shutdown(deadline), service.shutdown(deadline))
        assert first == second == ()

    asyncio.run(exercise())


def test_release_deadline_removes_handle_when_publisher_suppresses_cancellation(
    tmp_path: Path,
) -> None:
    class HoldingReleasePublisher(_Publisher):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.release = asyncio.Event()
            self.exited = asyncio.Event()

        async def publish_terminal_state(
            self, employee: Any, binding: Any, state: Any
        ) -> None:
            if state.lifecycle == "released":
                self.entered.set()
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        self.cancel_seen.set()
                self.exited.set()
            await super().publish_terminal_state(employee, binding, state)

    async def exercise() -> None:
        publisher = HoldingReleasePublisher()
        service = _service(
            tmp_path,
            publisher,
            cleanup_timeout_seconds=0.03,
        )
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "stream"],
            ),
        )
        await service.wait_for_terminal_exit(
            _employee(tmp_path),
            1,
            WaitForTerminalExitRequest(
                session_id="session-a", terminal_id="terminal-1"
            ),
        )
        started = asyncio.get_running_loop().time()
        release_task = asyncio.create_task(
            service.release_terminal(
                _employee(tmp_path),
                1,
                ReleaseTerminalRequest(
                    session_id="session-a", terminal_id="terminal-1"
                ),
            )
        )
        await publisher.entered.wait()
        with pytest.raises(AcpReverseTerminalError, match="release state"):
            await asyncio.wait_for(release_task, timeout=0.2)
        assert asyncio.get_running_loop().time() - started < 0.2
        assert publisher.cancel_seen.is_set()
        with pytest.raises(AcpReverseTerminalError):
            await service.terminal_output(
                _employee(tmp_path),
                1,
                TerminalOutputRequest(
                    session_id="session-a", terminal_id="terminal-1"
                ),
            )
        publisher.release.set()
        await asyncio.wait_for(publisher.exited.wait(), timeout=0.2)
        assert await service.shutdown(asyncio.get_running_loop().time() + 1) == ()

    asyncio.run(exercise())


def test_shutdown_deadline_detaches_cancellation_resistant_reader_and_watcher(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        publisher = _Publisher()
        service = _service(
            tmp_path,
            publisher,
            cleanup_timeout_seconds=0.03,
        )
        reader_entered = asyncio.Event()
        reader_cancel_seen = asyncio.Event()
        reader_release = asyncio.Event()
        reader_exited = asyncio.Event()

        async def cancellation_resistant_reader(_handle: Any) -> None:
            reader_entered.set()
            while not reader_release.is_set():
                try:
                    await reader_release.wait()
                except asyncio.CancelledError:
                    reader_cancel_seen.set()
            reader_exited.set()

        service_with_test_reader: Any = service
        service_with_test_reader._read_output = cancellation_resistant_reader
        await service.create_terminal(
            _employee(tmp_path),
            1,
            CreateTerminalRequest(
                session_id="session-a",
                command=sys.executable,
                args=[str(FIXTURE), "hold"],
            ),
        )
        await reader_entered.wait()
        started = asyncio.get_running_loop().time()
        unfinished = await asyncio.wait_for(
            service.shutdown(started + 0.03), timeout=0.2
        )
        assert unfinished == ("terminal-1",)
        assert asyncio.get_running_loop().time() - started < 0.2
        assert reader_cancel_seen.is_set()
        with pytest.raises(AcpReverseTerminalError):
            await service.terminal_output(
                _employee(tmp_path),
                1,
                TerminalOutputRequest(
                    session_id="session-a", terminal_id="terminal-1"
                ),
            )
        reader_release.set()
        await asyncio.wait_for(reader_exited.wait(), timeout=0.2)

    asyncio.run(exercise())


class _NoopStrategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError((args, kwargs))

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError((args, kwargs))


def _sdk_terminal_definition() -> AgentBackendDefinition:
    scripted_agent = Path(__file__).resolve().parents[1] / "support" / "acp_scripted_agent.py"
    return AgentBackendDefinition(
        backend_key="scripted-terminal",
        argv=(sys.executable, str(scripted_agent)),
        inherited_environment_names=tuple(default_environment()),
        environment_overrides=(),
        expected_agent_name="panels-scripted-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(
            supports_steer=False, observes_compaction=False
        ),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=False, terminal=True, permission=False
        ),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=_NoopStrategy(),
    )


def test_official_sdk_bridge_executes_declared_terminal_calls(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        employee = _employee(tmp_path)
        live_binding: ConversationSessionBinding | None = None
        publisher = _Publisher()

        async def resolve(
            candidate: ConversationEmployee, generation: int
        ) -> ConversationSessionBinding | None:
            assert candidate == employee
            return live_binding if generation == 1 else None

        service = ScopedAcpTerminalService(
            resolve,
            publisher,
            terminal_enabled=True,
            terminal_id_factory=lambda: "sdk-terminal",
            base_environment={"BASE": "base"},
            default_working_directory=lambda candidate: candidate.workspace_roots[0],
            output_max_bytes=128,
            cleanup_timeout_seconds=1,
        )
        definition = _sdk_terminal_definition()
        updates: list[Any] = []
        deaths: list[BaseException | None] = []

        async def sink(item: Any) -> None:
            updates.append(item)

        async def permission(_request: Any) -> RequestPermissionResponse:
            raise AssertionError("permission was not declared")

        async def death(error: BaseException | None) -> None:
            deaths.append(error)

        child = await SdkAcpEmployeeChildFactory(
            definition,
            panels_server_url="http://127.0.0.1:8767",
            terminal_service=service,
        ).create(employee, 1, sink, permission, death)
        try:
            await child.initialize(build_panels_initialize_request(definition))
            session = await child.new_session(
                NewSessionRequest(cwd=str(tmp_path), mcp_servers=[])
            )
            live_binding = ConversationSessionBinding(
                employee_id=employee.employee_id,
                acp_session_id=session.session_id,
                backend_key=employee.backend_key,
                binding_generation=1,
            )
            response = await child.prompt(
                PromptRequest(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="use terminal")],
                    field_meta={
                        "script": "reverse_terminal",
                        "fixture": str(FIXTURE),
                        "cwd": str(tmp_path),
                    },
                )
            )
            assert response.stop_reason == "end_turn"
            assert any(
                getattr(getattr(item.update, "content", None), "text", None)
                == "one-é-three|0|False"
                for item in updates
            )
            assert publisher.states[-1].lifecycle == "released"
        finally:
            await child.close()
            await service.shutdown(asyncio.get_running_loop().time() + 1)
        assert deaths == [None]

    asyncio.run(exercise())


def test_declared_terminal_requires_complete_service_before_spawn(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        definition = _sdk_terminal_definition()

        async def discard(_item: Any) -> None:
            return None

        async def permission(_request: Any) -> RequestPermissionResponse:
            raise AssertionError

        async def death(_error: BaseException | None) -> None:
            return None

        with pytest.raises(AcpChildUnsupportedReverseService):
            await SdkAcpEmployeeChildFactory(
                definition, panels_server_url="http://127.0.0.1:8767"
            ).create(
                _employee(tmp_path), 1, discard, permission, death
            )

    asyncio.run(exercise())
