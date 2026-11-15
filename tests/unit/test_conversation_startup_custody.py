"""A startup crash cannot permit replacement of a surviving native backend."""
from __future__ import annotations

import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.test_conversation_system import _Harness

from planner.conversation import child_custody
from planner.conversation.contracts import PromptDeliveryStarted
from planner.conversation.message_content import text_message_content
from planner.core.contracts import PlannerError

_HOME = '''
import asyncio
from pathlib import Path
import sys
from tests.unit.test_conversation_system import _Harness, _start
from planner.conversation.backends.hermes_acp import AcpChildLaunch, HermesAcpBackendChild
from planner.conversation.contracts import ConversationRoleMaterials
from planner.conversation.message_content import text_message_content
from planner.conversation.system import _CoreBackendEventSink
from planner.core.db import connect, create_schema
async def home():
    root = Path(sys.argv[1])
    conn = connect(str(root / 'home.db'))
    create_schema(conn)
    conn.close()
    if sys.argv[2] == 'pending':
        async def pause(self, identity):
            await asyncio.Event().wait()
        _CoreBackendEventSink.record_local_child_custody = pause
    def wrapper(original):
        def factory(*, resolved_start, event_sink, message_files):
            return HermesAcpBackendChild(
                launch=AcpChildLaunch((sys.executable, str(root / 'stub.py'),
                                      str(root / 'child.pid'))),
                resolved_start=resolved_start, event_sink=event_sink, message_files=message_files)
        return factory
    harness = _Harness(root / 'home.db', factory_wrapper=wrapper)
    await _start(harness, workspace_folder=root, role_materials=ConversationRoleMaterials(
        role_text='Manager', identity_environment_variables=(('PLAN_ACTOR', 'manager'),)))
    await harness.system.send('c', text_message_content('work'), sender_label='owner')
asyncio.run(home())
'''


def _wait(predicate: Callable[[], bool], process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 15
    while not predicate():
        assert process.poll() is None, process.communicate()
        assert time.monotonic() < deadline, 'Startup did not reach the crash boundary'
        time.sleep(0.01)


@pytest.mark.skipif(sys.platform != 'linux', reason='Real Linux crash and exact pidfd cleanup')
@pytest.mark.parametrize('record_identity', [True, False])
def test_crash_before_protocol_startup_retains_process_custody(
    tmp_path: Path, record_identity: bool,
) -> None:
    """Real spawn/DB/crash boundary: a fake child cannot prove surviving process custody."""
    (tmp_path / 'stub.py').write_text(
        'import os,sys,time\nfrom pathlib import Path\n'
        'Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(60)\n'
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith('PLAN_')}
    environment['PLAN_PROVISION_ACCOUNT_SKILLS'] = 'false'
    process = subprocess.Popen(
        [sys.executable, '-c', _HOME, str(tmp_path), 'record' if record_identity else 'pending'],
        env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    identity = None
    host = child_custody.host_boot_identity()
    assert host is not None
    try:
        _wait(lambda: (tmp_path / 'child.pid').exists(), process)
        pid = int((tmp_path / 'child.pid').read_text())
        identity = child_custody.process_identity(pid)
        assert identity is not None
        if record_identity:
            def recorded() -> bool:
                with sqlite3.connect(tmp_path / 'home.db') as conn:
                    return bool(conn.execute(
                        "SELECT state FROM conversation_child_custody WHERE conversation_id='c'"
                    ).fetchone() == ('started',))
            _wait(recorded, process)
        process.send_signal(signal.SIGKILL)
        process.communicate(timeout=10)
        assert not child_custody.child_is_absent(identity, host.host)

        async def recover() -> None:
            harness = _Harness(tmp_path / 'home.db')
            try:
                assert 'c' in harness.store.blocked_local_conversations
                if record_identity:
                    await harness.system.kill('c')
                    assert child_custody.child_is_absent(identity, host.host)
                    assert isinstance(await harness.system.send(
                        'c', text_message_content('replacement'), sender_label='owner',
                    ), PromptDeliveryStarted)
                    assert harness.spawned_conversation_ids == ['c']
                else:
                    with pytest.raises(PlannerError, match='Reboot this same host'):
                        await harness.system.kill('c')
                    await harness.system.send(
                        'c', text_message_content('replacement'), sender_label='owner',
                    )
                    assert harness.spawned_conversation_ids == []
                    assert 'c' in harness.store.blocked_local_conversations
                    assert not child_custody.child_is_absent(identity, host.host)
            finally:
                await harness.system.shutdown()
        asyncio.run(recover())
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
        if identity is not None:
            assert child_custody.stop_recorded_child(identity, host.host)


def test_identity_free_retirement_requires_same_host_and_new_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.conversation.local_child_contracts import LocalHostBootIdentity

    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE custody(state,supervision_record,host,boot)')
    conn.execute("INSERT INTO custody VALUES('launch_pending',NULL,'home','old')")
    row = conn.execute('SELECT * FROM custody').fetchone()
    for host, boot, expected in [('home', 'old', False), ('other', 'new', False),
                                 ('home', 'new', True)]:
        monkeypatch.setattr(child_custody, 'host_boot_identity',
                            lambda host=host, boot=boot: LocalHostBootIdentity(host, boot))
        assert child_custody.retirement_row_proven(row) == expected
    conn.close()


def test_failed_start_keeps_handle_until_native_cleanup_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identity write failure plus failed cleanup cannot permit a second local child."""
    from unittest.mock import AsyncMock

    from tests.unit.test_conversation_system import _start

    from planner.conversation.backends.contracts import BackendSpawnFailed
    from planner.core.db import connect, create_schema

    conn = connect(str(tmp_path / 'home.db'))
    create_schema(conn)
    conn.close()
    children = []

    def wrapper(factory):  # type: ignore[no-untyped-def]
        def create(**kwargs):  # type: ignore[no-untyped-def]
            child = factory(**kwargs)
            original_start = child.start
            async def fail_after_spawn(*args, **kw):  # type: ignore[no-untyped-def]
                await original_start(*args, **kw)
                raise BackendSpawnFailed('custody persistence failed')
            monkeypatch.setattr(child, 'start', fail_after_spawn)
            monkeypatch.setattr(
                child, 'stop', AsyncMock(side_effect=OSError('native cleanup failed'))
            )
            children.append(child)
            return child
        return create

    async def exercise() -> None:
        harness = _Harness(tmp_path / 'home.db', factory_wrapper=wrapper)
        try:
            await _start(harness)
            await harness.system.send('c', text_message_content('first'), sender_label='owner')
            assert harness.system._conversations['c'].child_is_quarantined
            await harness.system.send('c', text_message_content('second'), sender_label='owner')
            assert len(children) == 1
            assert children[0] is harness.system._conversations['c'].child
            assert children[0] is not None
            with pytest.raises(PlannerError, match='Recheck Worker cleanup') as refusal:
                await harness.system.kill('c')
            assert refusal.value.code.value == 'retry_later'
            assert harness.system._conversations['c'].child_is_quarantined
            assert len(children) == 1
            monkeypatch.setattr(children[0], 'stop', AsyncMock())
            await harness.system.kill('c')
            assert await harness.store.recorded_source_retirement_proven('c')
        finally:
            if children:
                assert children[0] is not None
                monkeypatch.setattr(children[0], 'stop', AsyncMock())
            await harness.system.shutdown()
    asyncio.run(exercise())


def test_missing_host_boot_identity_refuses_before_backend_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown host cannot leave an unrecoverable pending fence after a crash."""
    from unittest.mock import AsyncMock

    from tests.unit.test_conversation_system import _start

    from planner.conversation.contracts import PromptDeliveryRefusalReason, PromptDeliveryRefused
    from planner.conversation.events import PromptDeliveryRefusedEventPayload
    from planner.core.db import connect, create_schema

    db_path = str(tmp_path / "home.db")
    conn = connect(db_path)
    create_schema(conn)
    conn.close()
    starts = []

    def wrapper(factory):  # type: ignore[no-untyped-def]
        def create(**kwargs):  # type: ignore[no-untyped-def]
            child = factory(**kwargs)
            start = AsyncMock(wraps=child.start)
            monkeypatch.setattr(child, "start", start)
            starts.append(start)
            return child
        return create

    async def exercise() -> None:
        harness = _Harness(Path(db_path), factory_wrapper=wrapper)
        try:
            monkeypatch.setattr(child_custody, "host_boot_identity", lambda: None)
            await _start(harness)
            result = await harness.system.send(
                "c", text_message_content("work"), sender_label="owner",
            )
            assert isinstance(result, PromptDeliveryRefused)
            assert result.refusal_reason is PromptDeliveryRefusalReason.backend_did_not_start
            assert len(starts) == 1
            starts[0].assert_not_awaited()
            failures = [event.payload for event in await harness.events("c")
                        if isinstance(event.payload, PromptDeliveryRefusedEventPayload)]
            assert failures[0].refusal_detail is not None
            assert "host and boot identity" in failures[0].refusal_detail
            with connect(db_path) as conn:
                row = conn.execute("SELECT state FROM conversation_child_custody").fetchone()
                assert row[0] == "retired"
        finally:
            await harness.system.shutdown()

    asyncio.run(exercise())
