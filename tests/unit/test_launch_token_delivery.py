"""The native launch boundary supplies credentials without saving their plaintext."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException
from tests.support.conversation_claude_agent_sdk_bench import _RecordingSink
from tests.support.launch_identity import agent_headers

from planner.agent_identity.contracts import (
    LAUNCH_TOKEN_ENVIRONMENT_NAME,
    UNIDENTIFIED_LAUNCH_TOKEN,
)
from planner.agent_identity.data import authenticate_launch_token
from planner.agent_identity.launch import local_launch_start
from planner.conversation.api import StartConversationBody, _start_request
from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.backends.codex_app_server.client import child_environment
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendEventSink,
    RetainedBackendChildFactory,
)
from planner.conversation.backends.hermes_acp import AcpChildLaunch, _child_environment
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationSystem,
    ResolvedConversationStart,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ensure_started_conversation_record
from planner.core.contracts import CHIEF_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.executor.routing import LocaleBackendChildFactory
from planner.machines.contracts import ORIGINAL_MAC_MACHINE_ID
from planner.runtime.conversation_start import reset_agent_conversation
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def _start(
    tmp_path: Path, backend: ConversationBackendKey = ConversationBackendKey.codex
) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id="launch-proof",
        backend_key=backend,
        model="proof-model",
        reasoning_effort=None,
        workspace_folder=tmp_path,
        access=ConversationAccess.full,
        # A wrong saved role must not affect the canonical identity.
        role_materials=ConversationRoleMaterials("proof", (("PLAN_ACTOR", "worker"),)),
    )


def _database(tmp_path: Path, start: ResolvedConversationStart) -> str:
    path = str(tmp_path / "launch.db")
    conn = connect(path)
    try:
        create_schema(conn)
        ensure_started_conversation_record(conn, start, created_at=1)
        agent_headers(conn, "chief", conversation_id=start.conversation_id)
    finally:
        conn.close()
    return path


def test_local_child_restart_rotates_token_without_saving_the_secret(tmp_path: Path) -> None:
    start = _start(tmp_path)
    database = _database(tmp_path, start)

    class RecordingChild:
        launches: list[ResolvedConversationStart] = []

        async def start(
            self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
        ) -> None:
            self.launches.append(resolved_start)

    child = RecordingChild()

    def native_factory(
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> BackendChild:
        assert resolved_start.role_materials is not None
        assert LAUNCH_TOKEN_ENVIRONMENT_NAME not in dict(
            resolved_start.role_materials.identity_environment_variables
        )
        return cast(BackendChild, child)

    factory = LocaleBackendChildFactory(
        database,
        native_factory,
        cast(RetainedBackendChildFactory, native_factory),
    )

    async def exercise() -> None:
        for cursor in (None, "saved-native-session"):
            local_child = factory(
                resolved_start=start,
                event_sink=_RecordingSink(),
                message_files=ConversationMessageFiles(database),
            )
            # Recreate the production wrapper as server recovery does, from the
            # same nonsecret durable start and the saved native-session cursor.
            await local_child.start(start, vendor_session_cursor=cursor)

    asyncio.run(exercise())
    first, second = [
        dict(launch.role_materials.identity_environment_variables)[LAUNCH_TOKEN_ENVIRONMENT_NAME]
        for launch in child.launches
        if launch.role_materials
    ]
    assert first != second
    conn = connect(database)
    try:
        assert authenticate_launch_token(conn, first) is None
        identity = authenticate_launch_token(conn, second)
        assert identity is not None and identity.principal == CHIEF_PRINCIPAL
        assert identity.conversation_id == start.conversation_id
        saved = conn.execute("SELECT identity_environment_variables FROM conversations").fetchone()[
            0
        ]
        assert LAUNCH_TOKEN_ENVIRONMENT_NAME not in saved
        assert first not in saved and second not in saved

        class ResetSystem:
            killed: list[str] = []

            async def kill(self, conversation_id: str) -> None:
                self.killed.append(conversation_id)

        reset_system = ResetSystem()
        asyncio.run(
            reset_agent_conversation(
                cast(ConversationSystem, reset_system),
                conn,
                "chief_of_staff",
            )
        )
        assert reset_system.killed == [start.conversation_id]
        assert authenticate_launch_token(conn, second) is None
        assert conn.execute("SELECT 1 FROM agent_launch_tokens").fetchone() is None
    finally:
        conn.close()


def test_unassociated_native_child_gets_no_inherited_authority(tmp_path: Path) -> None:
    start = _start(tmp_path)
    database = _database(tmp_path, start)
    launched = local_launch_start(database, replace(start, conversation_id="unassociated"))
    assert launched.role_materials is not None
    assert (
        dict(launched.role_materials.identity_environment_variables)[LAUNCH_TOKEN_ENVIRONMENT_NAME]
        == UNIDENTIFIED_LAUNCH_TOKEN
    )


def test_generic_start_and_storage_refuse_identity_injection(tmp_path: Path) -> None:
    with pytest.raises(HTTPException) as refused:
        _start_request(
            StartConversationBody(
                conversation_id="injected",
                model="proof-model",
                role_text="Chief",
                identity_environment_variables={"PLAN_ACTOR": "chief"},
            )
        )
    assert refused.value.status_code == 422
    start = _start(tmp_path)
    database = _database(tmp_path, start)
    conn = connect(database)
    try:
        with pytest.raises(ValueError, match="transient"):
            ensure_started_conversation_record(
                conn,
                replace(
                    start,
                    conversation_id="injected",
                    role_materials=ConversationRoleMaterials(
                        "Chief", ((LAUNCH_TOKEN_ENVIRONMENT_NAME, "raw-secret"),)
                    ),
                ),
                created_at=1,
            )
        assert (
            conn.execute("SELECT 1 FROM conversations WHERE conversation_id='injected'").fetchone()
            is None
        )
    finally:
        conn.close()


@pytest.mark.parametrize("backend", list(ConversationBackendKey))
def test_native_environment_delivers_the_credential_to_cli_in_a_real_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: ConversationBackendKey,
) -> None:
    """Environment-builder -> OS child -> CLI is the gap dictionary assertions leave.

    Three short Python children reuse the existing native environment builders; no
    browser, network connection, vendor model call, or additional backend host is needed.
    """
    start = _start(tmp_path, backend)
    database = _database(tmp_path, start)
    monkeypatch.setenv(LAUNCH_TOKEN_ENVIRONMENT_NAME, "another-conversations-token")
    launched = local_launch_start(database, start)

    def environment(value: ResolvedConversationStart) -> dict[str, str]:
        if backend is ConversationBackendKey.codex:
            return child_environment(
                identity=value.role_materials.identity_environment_variables
                if value.role_materials
                else ()
            )
        if backend is ConversationBackendKey.hermes:
            return _child_environment(AcpChildLaunch((sys.executable,)), value)
        child = ClaudeAgentSdkBackendChild(
            launch=ClaudeAgentSdkChildLaunch(),
            resolved_start=value,
            event_sink=_RecordingSink(),
            message_files=ConversationMessageFiles(database),
        )
        # The SDK merges its process environment with the explicit env options.
        return {**os.environ, **child._options(value, None, None).env}

    assert environment(start)[LAUNCH_TOKEN_ENVIRONMENT_NAME] == UNIDENTIFIED_LAUNCH_TOKEN
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import json; from planner.cli.http import _headers; print(json.dumps(_headers()))",
        ],
        env=environment(launched),
        capture_output=True,
        text=True,
        check=True,
    )
    headers: dict[str, Any] = json.loads(result.stdout)
    assert set(headers) == {"Authorization"}
    token = headers["Authorization"].removeprefix("Bearer ")
    conn = connect(database)
    try:
        identity = authenticate_launch_token(conn, token)
        assert identity is not None and identity.principal == CHIEF_PRINCIPAL
        assert identity.conversation_id == start.conversation_id
    finally:
        conn.close()


def test_stored_worker_context_cannot_select_remote_authority(tmp_path: Path) -> None:
    """A historical raw start may contain a real Mac Ticket ID without owning it."""
    start = _start(tmp_path)
    database = _database(tmp_path, start)
    conn = connect(database)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            title="Real Mac Ticket",
            principal=CHIEF_PRINCIPAL,
            now=10,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
            brief="Mac work",
            machine_id=ORIGINAL_MAC_MACHINE_ID,
        )
    finally:
        conn.close()
    spoofed = replace(
        start,
        role_materials=ConversationRoleMaterials(
            "Old raw start",
            (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id)),
        ),
    )
    selected: list[str] = []

    def local(**kwargs: Any) -> BackendChild:
        selected.append("local")
        return cast(BackendChild, object())

    def remote(**kwargs: Any) -> BackendChild:
        selected.append("remote")
        raise AssertionError("saved environment granted remote native capability")

    factory = LocaleBackendChildFactory(
        database,
        local,
        cast(RetainedBackendChildFactory, remote),
    )
    factory(
        resolved_start=spoofed,
        event_sink=_RecordingSink(),
        message_files=ConversationMessageFiles(database),
    )
    assert selected == ["local"]
