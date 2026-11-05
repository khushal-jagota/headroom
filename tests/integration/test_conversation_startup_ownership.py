"""A rejected runtime cannot reconcile its owner's still-live conversation."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests.unit.test_conversation_system import _Harness, _start

from planner.conversation.lifecycle import LifecycleKind, LifecycleStatus
from planner.conversation.message_content import text_message_content
from planner.core.db import connect, create_schema

_CLAIM_IN_ANOTHER_PROCESS = """
import sys
from planner.conversation.lifecycle_storage import ConversationRuntimeAlreadyOwned
from planner.conversation.storage import ConversationStore
try:
    ownership = ConversationStore(sys.argv[1]).claim_runtime()
except ConversationRuntimeAlreadyOwned:
    print('rejected')
else:
    ownership.close()
    print('claimed')
"""


def _create_database(path: Path) -> None:
    conn = connect(str(path))
    try:
        create_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("through_symlink", [False, True])
def test_a_second_process_cannot_invalidate_the_live_owner(
    tmp_path: Path, through_symlink: bool
) -> None:
    db_path = tmp_path / "planner.db"
    _create_database(db_path)
    second_path = db_path
    if through_symlink:
        second_path = tmp_path / "alias.db"
        second_path.symlink_to(db_path)
    child_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_IDENTITY_"))
    }

    def claim_in_child() -> str:
        result = subprocess.run(
            [sys.executable, "-c", _CLAIM_IN_ANOTHER_PROCESS, str(second_path)],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            env=child_env,
        )
        return result.stdout.strip()

    async def exercise() -> None:
        harness = _Harness(db_path)
        try:
            await _start(harness, "live-owner")
            await harness.system.send(
                "live-owner", text_message_content("stay active"), sender_label="owner"
            )
            await harness.raise_permission_ask("live-owner")
            before = await harness.store.lifecycle_facts("live-owner")
            assert any(fact.status is LifecycleStatus.running for fact in before)
            assert any(fact.status is LifecycleStatus.pending for fact in before)
            assert await asyncio.to_thread(claim_in_child) == "rejected"
            assert await harness.store.lifecycle_facts("live-owner") == before
            assert await harness.system.is_running("live-owner")
            assert await harness.system.has_pending_permission_ask("live-owner")
        finally:
            await harness.system.shutdown()
        assert await asyncio.to_thread(claim_in_child) == "claimed"
        after = await harness.store.lifecycle_facts("live-owner")
        assert all(
            fact.status is LifecycleStatus.unavailable
            for fact in after
            if fact.kind in (LifecycleKind.turn, LifecycleKind.request)
        )

    asyncio.run(asyncio.wait_for(exercise(), 30))
