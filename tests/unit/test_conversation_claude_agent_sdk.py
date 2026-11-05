"""What the claude adapter has to be true about, close to where it does it.

The conformance suite proves the contract from outside. The one obligation here that no
outside observer can see is that a resume which did not restore is never quietly replaced
by a fresh thread: downstream, a substitute session and the real one are indistinguishable,
and accepting one in silence loses the whole conversation's memory.

The rest of this file talks to the claude actually installed on this machine and costs real
model calls, so it is opt-in: set ``PANELS_REAL_CLAUDE_TESTS=1`` to run it. That is a
diagnostic to reach for when claude is suspected of having drifted, not day-to-day cover.

The bench these run on — the scripted client, the recording sink, the child around them —
lives in ``tests/support/conversation_claude_agent_sdk_bench.py``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from anyio.abc import Process
from claude_agent_sdk import (
    ClaudeAgentOptions,
    CLINotFoundError,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
    TextBlock,
)
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from tests.support.conversation_claude_agent_sdk_bench import (
    ANOTHER_SESSION_ID,
    SESSION_ID,
    _assistant,
    _bench,
    _run,
    _ScriptedClaudeSdkClient,
    _start_request,
    _write,
)

from planner.conversation.backends.claude_agent_sdk import (
    _ClaudeCleanupTransport,
    _ClaudeProtocolTransport,
    _ObservedClaudeSdkClient,
)
from planner.conversation.backends.contracts import (
    BackendSpawnFailed,
    BackendStreamEnded,
    BackgroundTaskEnded,
    BackgroundTaskStarted,
    NeedsRebind,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import ConversationRoleMaterials, PromptDeliveryMode
from planner.conversation.events import ConversationTurnEnding
from planner.conversation.message_content import text_message_content


def test_a_resume_that_answers_under_another_session_is_refused(tmp_path: Path) -> None:
    """A fresh thread standing in for this conversation's own is never accepted in silence.

    The cursor is left alone — writing the substitute down is exactly the silent acceptance
    this guards against — and the turn is failed saying what happened.
    """

    async def exercise() -> None:
        start = _start_request(
            workspace_folder=tmp_path,
            reasoning_effort="medium",
            role_materials=ConversationRoleMaterials("Worker", (("PLAN_ACTOR", "worker"),)),
        )
        child, sink, clients = _bench(start)
        await child.start(start, vendor_session_cursor=SESSION_ID)
        options = clients[0].options
        assert options.settings is not None
        assert json.loads(options.settings) == {"disableAllHooks": True}
        assert options.setting_sources is None
        assert options.cwd == str(tmp_path)
        assert options.cli_path == Path("/usr/bin/claude")
        assert options.model == start.model
        assert options.effort == "medium"
        assert options.env == {"PLAN_ACTOR": "worker"}
        assert options.permission_mode == "bypassPermissions"
        assert options.can_use_tool is not None
        assert options.resume == SESSION_ID
        assert options.session_id is None
        await _write(child)
        clients[0].say(_assistant(TextBlock(text="hi"), session_id=ANOTHER_SESSION_ID))
        await clients[0].until_taken_in()

        assert sink.cursors == []
        assert len(sink.endings) == 1
        assert sink.endings[0]["ending"] is ConversationTurnEnding.failed
        assert ANOTHER_SESSION_ID in str(sink.endings[0]["error_summary"])
        assert sink.message_texts == []
        # Nothing more is written to a child that is not this conversation's session.
        # The stored cursor remains safe to try on one replacement child.
        with pytest.raises(NeedsRebind):
            await _write(child, "again")
        await child.stop()

    _run(exercise)


def test_a_manager_wake_crosses_the_claude_adapter_with_its_sender_label(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        start = _start_request(
            workspace_folder=tmp_path,
            reasoning_effort="medium",
            role_materials=ConversationRoleMaterials("Worker", (("PLAN_ACTOR", "worker"),)),
        )
        child, _sink, clients = _bench(start)
        await child.start(start, vendor_session_cursor=None)
        options = clients[0].options
        assert options.settings is not None
        assert json.loads(options.settings) == {"disableAllHooks": True}
        assert options.setting_sources is None
        assert options.cwd == str(tmp_path)
        assert options.cli_path == Path("/usr/bin/claude")
        assert options.model == start.model
        assert options.effort == "medium"
        assert options.env == {"PLAN_ACTOR": "worker"}
        assert options.permission_mode == "bypassPermissions"
        assert options.can_use_tool is not None
        assert options.resume is None
        assert options.session_id is not None
        assert UUID(options.session_id).version == 4
        content = text_message_content("Review the manager wake.")
        await child.write_prompt(
            TurnToken(conversation_id="c-claude-1", turn_number=1),
            content,
            sender_content=content,
            sender_label="Panels",
            mode=PromptDeliveryMode.queue,
            model_change=None,
            reasoning_effort_change=None,
        )
        assert len(clients[0].prompts) == 1
        assert "Panels" in clients[0].prompts[0]
        assert "Review the manager wake." in clients[0].prompts[0]
        await child.stop()

    _run(exercise)


def test_a_claude_model_change_rebinds_the_child_and_preserves_the_conversation(
    tmp_path: Path,
) -> None:
    """A same-provider model change must cross Claude's fresh-child boundary."""
    from claude_agent_sdk import ResultMessage

    from planner.conversation.backends.claude_agent_sdk import (
        ClaudeAgentSdkBackendChildFactory,
        ClaudeAgentSdkChildLaunch,
    )
    from planner.conversation.contracts import (
        ConversationBackendKey,
        ConversationStartRequest,
        PromptDeliveryStarted,
    )
    from planner.conversation.live_tail import ConversationLiveTail
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.conversation.storage import ConversationStore
    from planner.conversation.system import SqliteProcessConversationSystem
    from planner.core.db import connect, create_schema

    db_path = str(tmp_path / "claude-model-change.db")
    conn = connect(db_path)
    create_schema(conn)
    conn.close()

    async def exercise() -> None:
        clients: list[_ScriptedClaudeSdkClient] = []

        def make(options: ClaudeAgentOptions) -> _ScriptedClaudeSdkClient:
            client = _ScriptedClaudeSdkClient(options)
            clients.append(client)
            return client

        store = ConversationStore(db_path)
        system = SqliteProcessConversationSystem(
            store=store,
            message_files=ConversationMessageFiles(db_path),
            backend_child_factories={
                key: ClaudeAgentSdkBackendChildFactory(
                    ClaudeAgentSdkChildLaunch(claude_executable=Path("/usr/bin/claude")),
                    client_factory=make,
                )
                for key in ConversationBackendKey
            },
            live_tail=ConversationLiveTail(),
        )

        async def finish(client: _ScriptedClaudeSdkClient, session: str) -> None:
            client.say(ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1,
                is_error=False, num_turns=1, session_id=session,
            ))
            await client.until_taken_in()
            await system.wait_until_quiescent()

        try:
            await system.start_conversation(ConversationStartRequest(
                conversation_id="claude-model-change", backend_key=ConversationBackendKey.claude,
                model="opus", workspace_folder=tmp_path,
            ))
            first = await system.send(
                "claude-model-change", text_message_content("First turn"), sender_label="owner",
            )
            assert isinstance(first, PromptDeliveryStarted)
            session = clients[0].options.session_id
            assert session is not None
            await finish(clients[0], session)
            changed = await system.send(
                "claude-model-change", text_message_content("Use Sonnet"),
                sender_label="owner", model_change="sonnet",
            )
            assert isinstance(changed, PromptDeliveryStarted)
            assert [client.options.model for client in clients] == ["opus", "sonnet"]
            assert clients[0].disconnected
            assert len(clients[0].prompts) == 1
            assert clients[1].options.resume == session
            assert len(clients[1].prompts) == 1
            assert "Use Sonnet" in clients[1].prompts[0]
            record = await store.read_conversation("claude-model-change")
            assert record is not None
            assert record.model == "sonnet"
            assert record.vendor_session_cursor == session
            await finish(clients[1], session)
        finally:
            await system.shutdown()

    _run(exercise)


def test_background_tasks_follow_claudes_live_set(tmp_path: Path) -> None:
    """Claude's live set alone says which tasks are live, so an end it never announced
    still clears the task, and each end says how it happened. The frames follow claude's
    real order, including a terminated claude stopping its tasks before its stream ends.
    """

    def live(*task_ids: str, ambient: str | None = None) -> SystemMessage:
        tasks: list[dict[str, object]] = [
            {"task_id": task_id, "task_type": "local_bash", "description": f"sleep for {task_id}"}
            for task_id in task_ids
        ]
        if ambient is not None:
            watcher = {"task_id": ambient, "task_type": "monitor", "description": "watch"}
            tasks.append({**watcher, "ambient": True})
        return SystemMessage(
            subtype="background_tasks_changed",
            data={"type": "system", "subtype": "background_tasks_changed", "tasks": tasks},
        )

    def started(task_id: str) -> TaskStartedMessage:
        return TaskStartedMessage(
            subtype="task_started",
            data={},
            task_id=task_id,
            description=f"sleep for {task_id}",
            uuid=f"u-{task_id}",
            session_id=SESSION_ID,
        )

    def notified(task_id: str, status: str) -> TaskNotificationMessage:
        return TaskNotificationMessage(
            subtype="task_notification",
            data={},
            task_id=task_id,
            status=status,  # type: ignore[arg-type]
            output_file="/tmp/out",
            summary="",
            uuid=f"n-{task_id}",
            session_id=SESSION_ID,
        )

    def updated(task_id: str, status: str) -> TaskUpdatedMessage:
        return TaskUpdatedMessage(
            subtype="task_updated",
            data={},
            task_id=task_id,
            patch={"status": status},
            status=status,  # type: ignore[arg-type]
            session_id=SESSION_ID,
        )

    async def exercise() -> None:
        start = _start_request(workspace_folder=tmp_path)
        child, sink, clients = _bench(start)
        await child.start(start, vendor_session_cursor=SESSION_ID)
        clients[0].say(
            live("a", ambient="w"),
            started("a"),
            updated("a", "completed"),
            notified("a", "completed"),
            live(ambient="w"),
            # Stopped by the agent, which goes on working: the stop is the task's own end.
            live("b"),
            started("b"),
            updated("b", "killed"),
            live(),
            _assistant(TextBlock(text="stopped it")),
            # A shell whose end claude never announced leaves the live set all the same.
            live("e"),
            started("e"),
            live(),
            _assistant(TextBlock(text="carrying on")),
            # A watcher claude marks ambient stops being activity while it goes on.
            live("m"),
            live(ambient="m"),
            # Stopped while claude itself is being terminated: the stream ends next.
            live("c"),
            started("c"),
            notified("c", "stopped"),
            updated("c", "killed"),
            live(),
        )
        clients[0].end_the_stream()
        await clients[0].until_taken_in()

        assert sink.session_news == [
            BackgroundTaskStarted(task_id="a", task_description="sleep for a"),
            BackgroundTaskEnded(task_id="a", task_status="completed"),
            BackgroundTaskStarted(task_id="b", task_description="sleep for b"),
            BackgroundTaskEnded(task_id="b", task_status="killed"),
            BackgroundTaskStarted(task_id="e", task_description="sleep for e"),
            BackgroundTaskEnded(task_id="e", task_status="unreported"),
            BackgroundTaskStarted(task_id="m", task_description="sleep for m"),
            BackgroundTaskEnded(task_id="m", task_status="ambient"),
            BackgroundTaskStarted(task_id="c", task_description="sleep for c"),
            BackgroundTaskEnded(task_id="c", task_status="killed"),
            BackendStreamEnded(),
        ]
        await child.stop()

    _run(exercise)


# --- the claude on this machine ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("failure", "cursor", "expected_error"),
    [
        (CLINotFoundError("Claude executable is absent"), SESSION_ID, BackendSpawnFailed),
        (RuntimeError("Not logged in. Run /login."), None, BackendSpawnFailed),
        (RuntimeError("Requested session is unavailable"), SESSION_ID, SessionLoadFailed),
    ],
)
def test_unavailable_claude_start_preserves_adapter_failure_and_sends_no_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    cursor: str | None,
    expected_error: type[Exception],
) -> None:
    async def refuse_connect(self: _ScriptedClaudeSdkClient) -> None:
        raise failure

    monkeypatch.setattr(_ScriptedClaudeSdkClient, "connect", refuse_connect)

    async def exercise() -> None:
        start = _start_request(workspace_folder=tmp_path)
        child, sink, clients = _bench(start)
        try:
            with pytest.raises(expected_error) as refusal:
                await child.start(start, vendor_session_cursor=cursor)
            assert str(refusal.value) == str(failure)
            assert refusal.value.__cause__ is failure
            assert len(clients) == 1
            assert clients[0].prompts == []
            assert clients[0].streamed_messages == []
            assert sink.cursors == []
            assert sink.endings == []
        finally:
            await child.stop()

    _run(exercise)


@pytest.mark.parametrize("failure", [RuntimeError("disconnect failed"), asyncio.CancelledError()])
def test_failed_disconnect_retains_client_and_fences_prompt_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: BaseException
) -> None:
    async def exercise() -> None:
        start = _start_request(workspace_folder=tmp_path)
        child, _sink, clients = _bench(start)
        await child.start(start, vendor_session_cursor=None)
        client = clients[0]
        original_disconnect = client.disconnect

        async def fail() -> None:
            raise failure

        monkeypatch.setattr(client, "disconnect", fail)
        with pytest.raises(type(failure)):
            await child.stop()
        assert child._client is client
        assert child._reader is None
        with pytest.raises(NeedsRebind):
            await _write(child, "must not reach provider")
        with pytest.raises(BackendSpawnFailed, match="cleanup handles"):
            await child.start(start, vendor_session_cursor=None)
        assert len(clients) == 1
        assert client.prompts == []
        monkeypatch.setattr(client, "disconnect", original_disconnect)
        await child.stop()
        await child.stop()
        assert child._client is None
        assert client.disconnected

    _run(exercise)


def test_prequery_connect_failure_cannot_skip_retained_transport_exit_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual SDK drops a pre-query transport without closing its live process."""
    async def exercise() -> None:
        options = ClaudeAgentOptions()
        transport = _ClaudeCleanupTransport("", options)
        observed = _ObservedClaudeSdkClient(options, _ClaudeProtocolTransport(transport))
        process = SimpleNamespace(returncode=None)
        closes = 0

        async def partial_connect(self: SubprocessCLITransport) -> None:
            self._process = cast(Process, process)
            raise RuntimeError("post-spawn pre-query failure")

        async def sdk_close(self: SubprocessCLITransport) -> None:
            nonlocal closes
            closes += 1
            self._process = None

        monkeypatch.setattr(SubprocessCLITransport, "connect", partial_connect)
        monkeypatch.setattr(SubprocessCLITransport, "close", sdk_close)
        with pytest.raises(RuntimeError, match="post-spawn pre-query failure"):
            await observed.connect()
        assert observed._client._query is None
        assert transport._process is cast(Process, process)
        with pytest.raises(RuntimeError, match="termination remains unconfirmed"):
            await observed.disconnect()
        # The SDK now lost its handle. Its no-op disconnect still cannot establish
        # exit for the exact process that our wrapper continues to own.
        assert observed._client._transport is None
        assert transport._cleanup_process is cast(Process, process)
        with pytest.raises(RuntimeError, match="termination remains unconfirmed"):
            await observed.disconnect()
        assert closes == 2
        process.returncode = 0
        await observed.disconnect()
        await observed.disconnect()
        assert closes == 4
        assert transport._cleanup_process is cast(Process, process)

    _run(exercise)


@pytest.mark.parametrize("admission", [True, False, None])
def test_ordinary_send_requires_observed_uuid_admission(
    tmp_path: Path, admission: bool | None,
) -> None:
    from planner.conversation.backends.contracts import BackendPromptRejected, PromptWriteFailed

    async def exercise() -> None:
        start = _start_request(workspace_folder=tmp_path)
        child, _sink, clients = _bench(start)
        await child.start(start, vendor_session_cursor=None)
        clients[0].steer_admission = admission
        content = text_message_content("exact request")
        try:
            if admission is True:
                result = await child.write_prompt(
                    TurnToken("c-claude-1", 1), content, sender_content=content,
                    sender_label="owner", mode=PromptDeliveryMode.queue,
                    model_change=None, reasoning_effort_change=None,
                )
                assert result.composed_content_delivered is True
            else:
                expected = BackendPromptRejected if admission is False else PromptWriteFailed
                with pytest.raises(expected):
                    await _write(child)
            assert len(clients[0].watched_user_message_uuids) == 1
        finally:
            await child.stop()
    _run(exercise)


def test_observed_admission_survives_subsequent_query_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        start = _start_request(workspace_folder=tmp_path)
        child, sink, clients = _bench(start)
        await child.start(start, vendor_session_cursor=None)
        client = clients[0]
        original = client.query

        async def accepted_then_failed(prompt: object) -> None:
            await original(prompt)  # type: ignore[arg-type]
            raise RuntimeError("wire failed after provider admission")

        monkeypatch.setattr(client, "query", accepted_then_failed)
        content = text_message_content("request")
        try:
            result = await child.write_prompt(
                TurnToken("c-claude-1", 1), content, sender_content=content,
                sender_label="owner", mode=PromptDeliveryMode.queue,
                model_change=None, reasoning_effort_change=None,
            )
            assert result.composed_content_delivered is True
            assert sink.endings[0]["ending"] is ConversationTurnEnding.failed
        finally:
            await child.stop()
    _run(exercise)
