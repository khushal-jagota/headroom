from __future__ import annotations

import asyncio
import os
from pathlib import Path

from planner.core.path_observer import observe_path_changes


def test_observer_emits_for_create_atomic_replace_and_delete_but_not_unchanged(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        path = tmp_path / "deployment-lifecycle.json"
        changed = asyncio.Event()
        calls: list[None] = []

        def emit() -> None:
            calls.append(None)
            changed.set()

        task = asyncio.create_task(observe_path_changes(path, emit, poll_seconds=0.01))
        try:
            await asyncio.sleep(0.03)
            assert calls == []

            path.write_text("one", encoding="utf-8")
            await asyncio.wait_for(changed.wait(), timeout=0.5)
            assert len(calls) == 1

            changed.clear()
            replacement = tmp_path / ".replacement"
            replacement.write_text("two", encoding="utf-8")
            os.replace(replacement, path)
            await asyncio.wait_for(changed.wait(), timeout=0.5)
            assert len(calls) == 2

            changed.clear()
            await asyncio.sleep(0.03)
            assert not changed.is_set()

            path.unlink()
            await asyncio.wait_for(changed.wait(), timeout=0.5)
            assert len(calls) == 3
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(exercise())
