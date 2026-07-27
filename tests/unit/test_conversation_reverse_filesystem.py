from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from acp.exceptions import RequestError
from acp.schema import (
    NewSessionRequest,
    PromptRequest,
    ReadTextFileRequest,
    RequestPermissionResponse,
    TextContentBlock,
    WriteTextFileRequest,
)
from acp.transports import default_environment

from planner.conversation.backend_contracts import (
    AgentBackendDefinition,
    BackendTurnCapabilities,
    ReverseServiceCapabilities,
)
from planner.conversation.contracts import ConversationEmployee, ConversationSessionBinding
from planner.conversation.reverse_services.filesystem import (
    AcpReverseFilesystemError,
    ConfinedAcpFilesystemService,
)
from planner.conversation.sdk_child import (
    AcpChildUnsupportedReverseService,
    SdkAcpEmployeeChildFactory,
    build_panels_initialize_request,
)

SCRIPTED_AGENT = Path(__file__).resolve().parents[1] / "support" / "acp_scripted_agent.py"


def _employee(root: Path) -> ConversationEmployee:
    return ConversationEmployee(
        employee_id="employee-a",
        entity_kind="ticket",
        entity_id="ticket-a",
        workspace_roots=(root,),
        backend_key="fake",
    )


def _binding() -> ConversationSessionBinding:
    return ConversationSessionBinding(
        employee_id="employee-a",
        acp_session_id="session-a",
        backend_key="fake",
        binding_generation=1,
    )


def _service(root: Path, *, enabled: bool = True) -> ConfinedAcpFilesystemService:
    async def resolve(employee: ConversationEmployee, generation: int):
        assert employee.workspace_roots == (root,)
        return _binding() if generation == 1 else None

    return ConfinedAcpFilesystemService(resolve, filesystem_enabled=enabled)


def test_exact_line_limit_and_replacement_write_semantics(tmp_path: Path) -> None:
    async def exercise() -> None:
        path = tmp_path / "notes.txt"
        path.write_bytes(b"one\r\ntwo\nthree")
        service = _service(tmp_path)
        response = await service.read_text_file(
            _employee(tmp_path),
            1,
            ReadTextFileRequest(session_id="session-a", path=str(path), line=2, limit=1),
        )
        assert response.content == "two\n"
        empty = await service.read_text_file(
            _employee(tmp_path),
            1,
            ReadTextFileRequest(session_id="session-a", path=str(path), line=1, limit=0),
        )
        assert empty.content == ""
        await service.write_text_file(
            _employee(tmp_path),
            1,
            WriteTextFileRequest(session_id="session-a", path=str(path), content="é\nexact"),
        )
        assert path.read_bytes() == "é\nexact".encode()
        service.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "kind", ["relative", "traversal", "sibling", "directory", "wrong-session", "wrong-generation"]
)
def test_escape_and_scope_cases_fail_closed(tmp_path: Path, kind: str) -> None:
    async def exercise() -> None:
        root = tmp_path / "work"
        root.mkdir()
        sibling = tmp_path / "work-other"
        sibling.mkdir()
        target = root / "file.txt"
        target.write_text("safe")
        request_path = str(target)
        session = "session-a"
        generation = 1
        if kind == "relative":
            request_path = "file.txt"
        elif kind == "traversal":
            request_path = str(root / ".." / "work-other" / "file.txt")
        elif kind == "sibling":
            request_path = str(sibling / "file.txt")
        elif kind == "directory":
            request_path = str(root)
        elif kind == "wrong-session":
            session = "other"
        else:
            generation = 2
        service = _service(root)
        with pytest.raises(AcpReverseFilesystemError):
            await service.read_text_file(
                _employee(root),
                generation,
                ReadTextFileRequest(session_id=session, path=request_path),
            )
        service.close()

    asyncio.run(exercise())


def test_safe_internal_symlink_works_but_escape_and_dangling_symlinks_fail(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / "root"
        root.mkdir()
        inside = root / "inside.txt"
        inside.write_text("inside")
        outside = tmp_path / "outside.txt"
        outside.write_text("outside")
        safe = root / "safe-link"
        escape = root / "escape-link"
        dangling = root / "dangling-link"
        safe.symlink_to(inside)
        escape.symlink_to(outside)
        dangling.symlink_to(tmp_path / "missing")
        service = _service(root)
        result = await service.read_text_file(
            _employee(root),
            1,
            ReadTextFileRequest(session_id="session-a", path=str(safe)),
        )
        assert result.content == "inside"
        for path in (escape, dangling):
            with pytest.raises(AcpReverseFilesystemError):
                await service.write_text_file(
                    _employee(root),
                    1,
                    WriteTextFileRequest(session_id="session-a", path=str(path), content="bad"),
                )
        assert outside.read_text() == "outside"
        service.close()

    asyncio.run(exercise())


def test_descriptor_open_cannot_be_redirected_by_final_path_swap(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target.txt"
    target.write_text("safe")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    service = _service(root)
    confinement = service.confinement_for(_employee(root))
    descriptor = confinement.open_write_file(target)
    target.unlink()
    target.symlink_to(outside)
    try:
        os.write(descriptor, b"owned descriptor")
        os.ftruncate(descriptor, len(b"owned descriptor"))
    finally:
        os.close(descriptor)
    assert outside.read_text() == "outside"
    service.close()


def test_disabled_capability_and_invalid_utf8_fail(tmp_path: Path) -> None:
    async def exercise() -> None:
        path = tmp_path / "bad.txt"
        path.write_bytes(b"\xff")
        disabled = _service(tmp_path, enabled=False)
        with pytest.raises(AcpReverseFilesystemError):
            await disabled.read_text_file(
                _employee(tmp_path),
                1,
                ReadTextFileRequest(session_id="session-a", path=str(path)),
            )
        disabled.close()
        service = _service(tmp_path)
        with pytest.raises(AcpReverseFilesystemError):
            await service.read_text_file(
                _employee(tmp_path),
                1,
                ReadTextFileRequest(session_id="session-a", path=str(path), line=0),
            )
        with pytest.raises(AcpReverseFilesystemError):
            await service.read_text_file(
                _employee(tmp_path),
                1,
                ReadTextFileRequest(session_id="session-a", path=str(path)),
            )
        with pytest.raises(AcpReverseFilesystemError):
            await service.write_text_file(
                _employee(tmp_path),
                1,
                WriteTextFileRequest(
                    session_id="session-a",
                    path=str(tmp_path / "missing-parent" / "new.txt"),
                    content="must not create parents",
                ),
            )
        service.close()

    asyncio.run(exercise())


class _NoopStrategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError((args, kwargs))

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError((args, kwargs))


def _sdk_definition(*, filesystem: bool) -> AgentBackendDefinition:
    return AgentBackendDefinition(
        backend_key="scripted-filesystem",
        argv=(sys.executable, str(SCRIPTED_AGENT)),
        inherited_environment_names=tuple(default_environment()),
        environment_overrides=(),
        expected_agent_name="panels-scripted-agent",
        expected_agent_version="1.0.0",
        turn_capabilities=BackendTurnCapabilities(
            supports_steer=False, observes_compaction=False
        ),
        reverse_service_capabilities=ReverseServiceCapabilities(
            filesystem=filesystem, terminal=False, permission=False
        ),
        working_directory_resolver=lambda employee: employee.workspace_roots[0],
        turn_strategy=_NoopStrategy(),
    )


def test_official_sdk_bridge_executes_declared_filesystem_calls(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        employee = _employee(tmp_path)
        live_binding: ConversationSessionBinding | None = None

        async def resolve(
            candidate: ConversationEmployee, generation: int
        ) -> ConversationSessionBinding | None:
            assert candidate == employee
            return live_binding if generation == 1 else None

        service = ConfinedAcpFilesystemService(resolve, filesystem_enabled=True)
        definition = _sdk_definition(filesystem=True)
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
            filesystem_service=service,
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
            target = tmp_path / "bridge.txt"
            target.write_text("one\ntwo\nthree")
            response = await child.prompt(
                PromptRequest(
                    session_id=session.session_id,
                    prompt=[TextContentBlock(type="text", text="use filesystem")],
                    field_meta={"script": "reverse_filesystem", "path": str(target)},
                )
            )
            assert response.stop_reason == "end_turn"
            assert target.read_text() == "written through ACP"
            assert any(
                getattr(getattr(item.update, "content", None), "text", None)
                == "'two\\n'|'written through ACP'"
                for item in updates
            )
        finally:
            await child.close()
            service.close()
        assert deaths == [None]

    asyncio.run(exercise())


def test_declared_filesystem_requires_complete_service_before_spawn(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        definition = _sdk_definition(filesystem=True)

        async def permission(_request: Any) -> RequestPermissionResponse:
            raise AssertionError

        async def discard(_item: Any) -> None:
            return None

        async def death(_error: BaseException | None) -> None:
            return None

        with pytest.raises(AcpChildUnsupportedReverseService):
            await SdkAcpEmployeeChildFactory(
                definition, panels_server_url="http://127.0.0.1:8767"
            ).create(
                _employee(tmp_path),
                1,
                discard,
                permission,
                death,
            )

    asyncio.run(exercise())


def test_undeclared_filesystem_is_not_advertised_and_reverse_call_is_method_not_found(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        employee = _employee(tmp_path)
        live_binding: ConversationSessionBinding | None = None

        async def resolve(
            candidate: ConversationEmployee, generation: int
        ) -> ConversationSessionBinding | None:
            assert candidate == employee
            return live_binding if generation == 1 else None

        service = ConfinedAcpFilesystemService(resolve, filesystem_enabled=True)
        definition = _sdk_definition(filesystem=False)
        initialize = build_panels_initialize_request(definition)
        assert initialize.client_capabilities.fs is not None
        assert not initialize.client_capabilities.fs.read_text_file
        assert not initialize.client_capabilities.fs.write_text_file

        async def discard(_item: Any) -> None:
            return None

        async def permission(_request: Any) -> RequestPermissionResponse:
            raise AssertionError("permission was not declared")

        deaths: list[BaseException | None] = []

        async def death(error: BaseException | None) -> None:
            deaths.append(error)

        child = await SdkAcpEmployeeChildFactory(
            definition,
            panels_server_url="http://127.0.0.1:8767",
            filesystem_service=service,
        ).create(employee, 1, discard, permission, death)
        try:
            await child.initialize(initialize)
            session = await child.new_session(
                NewSessionRequest(cwd=str(tmp_path), mcp_servers=[])
            )
            live_binding = ConversationSessionBinding(
                employee_id=employee.employee_id,
                acp_session_id=session.session_id,
                backend_key=employee.backend_key,
                binding_generation=1,
            )
            target = tmp_path / "undeclared.txt"
            target.write_text("one\ntwo")
            with pytest.raises(RequestError) as raised:
                await child.prompt(
                    PromptRequest(
                        session_id=session.session_id,
                        prompt=[TextContentBlock(type="text", text="unexpected filesystem")],
                        field_meta={
                            "script": "reverse_filesystem",
                            "path": str(target),
                        },
                    )
                )
            assert raised.value.code == -32601
            assert raised.value.data == {"method": "fs/read_text_file"}
            assert target.read_text() == "one\ntwo"
        finally:
            await child.close()
            service.close()
        assert deaths == [None]

    asyncio.run(exercise())
