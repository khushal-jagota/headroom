"""Shared real Python runner setup and wire observations for the Mac direct proofs."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from contextlib import ExitStack
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from tests.compare.direct.mac_executor_fixture import run_node
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import ResolvedConversationStart
from planner.conversation.message_files import ConversationMessageFiles
from planner.executor import mac_mailbox, mac_runner
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import MacFilesApi
from planner.executor.mac_mailbox import CommandJournal
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_runner import MacLiveEvents, MacRunner, command_owes_outputs
from planner.executor.protocol import (
    ChildCommand,
    MailboxCommand,
    MailboxPollReply,
    QueuedEnvelope,
    as_object,
    type_adapter,
)


def make_runner(
    patch: pytest.MonkeyPatch,
    *,
    root: Path,
    now_ms: int,
    paths: ExecutorPaths,
    workspace_home: Path,
    factory: Callable[[ResolvedConversationStart], BackendChildFactory],
    files: MacFilesApi | None,
    live: MacLiveEvents | None,
) -> tuple[MacRunner, MacOutbox, CommandJournal]:
    patch.setattr(mac_runner, "now_ms", lambda: now_ms)
    patch.setattr(mac_mailbox, "now_ms", lambda: now_ms)
    patch.setattr(time, "time_ns", lambda: now_ms * 1_000_000)
    patch.setattr(
        mac_runner, "install_native_skill_snapshot", lambda *args, **kwargs: root / "skills"
    )
    with ExitStack() as cleanup:
        box = MacOutbox(root / "spool")
        cleanup.callback(box.close)
        journal = CommandJournal(root / "spool", "inc", outputs_owed=command_owes_outputs)
        cleanup.callback(journal.close)
        runner = MacRunner(
            incarnation="inc",
            outbox=box,
            journal=journal,
            factory=factory,
            files=files,
            live=live,
            message_files=cast(ConversationMessageFiles, None),
            paths=paths,
            workspace_home=workspace_home,
        )
        cleanup.pop_all()
        return runner, box, journal


def record_commands(
    journal: CommandJournal,
    commands: Mapping[str, ChildCommand],
    *,
    now_ms: int,
    unjournaled: Collection[str] = (),
) -> None:
    journal.record_poll_reply(
        MailboxPollReply(
            tuple(
                MailboxCommand(index, key, now_ms, child_command=command)
                for index, (key, command) in enumerate(commands.items(), 1)
                if key not in unjournaled
            )
        ),
        "inc",
    )


def pending_envelopes(box: MacOutbox) -> list[dict[str, Any]]:
    rows = [as_object(row) for row in box.pending()]
    assert len({row["submission_id"] for row in rows}) == len(rows)
    for index, row in enumerate(rows):
        assert UUID(cast(str, row["sender_id"])).hex == row["sender_id"]
        row["sender_id"] = "<sender>"
        submission = row["submission_id"]
        if row["kind"] == "backend_event" and re.fullmatch("[a-f0-9]{32}", cast(str, submission)):
            assert UUID(cast(str, submission)).hex == submission
            row["submission_id"] = f"event_{index}"
        type_adapter(QueuedEnvelope).validate_python(row)
    return rows


def paired_fixture(
    tmp_path: Path,
    patch: pytest.MonkeyPatch,
    *,
    reference: Callable[[Path, pytest.MonkeyPatch], Awaitable[dict[str, Any]]],
    inputs: Callable[[Path], dict[str, Any]],
    entry: str,
    python_timeout: float,
    node_timeout: int,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), patch.context() as local:
        expected = asyncio.run(asyncio.wait_for(reference(tmp_path / "py", local), python_timeout))
    target = tmp_path / "ts"
    data = inputs(target)
    target.mkdir(exist_ok=True)
    (target / "input.json").write_text(json.dumps(data))
    observed = run_node(entry, target, homes, timeout=node_timeout)
    (target / "expected.json").write_text(json.dumps(expected, indent=2))
    (target / "observed.json").write_text(json.dumps(observed, indent=2))
    return expected, observed, target
