"""Feature publication suppression on the current independent catalogue lanes.

Current adapter tests cover metadata freshness, source degradation, retained hints,
and recovery. These cases protect admission counts and fresh invisible invocation data.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from tests.support.conversation_codex_app_server_bench import _run, _scripted_child

from planner.conversation.backends.codex_app_server import adapter as catalog
from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.catalog import CatalogCoordinator
from planner.conversation.contracts import ComposerCatalogEntryKind


def test_publication_tracks_entries_but_installs_equal_fresh_invocations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as bench:
            snapshot = catalog.EMPTY_CATALOG_SNAPSHOT
            monkeypatch.setattr(catalog, "_catalog_snapshot", lambda *args: snapshot)
            await bench.child._publish_catalog()
            await bench.child._publish_catalog()
            assert bench.sink.composer_catalog_reports == [()]
            entries = catalog.CODEX_BUILT_IN_CATALOG_ENTRIES
            snapshot = catalog._CatalogSnapshot(entries)
            await bench.child._publish_catalog()
            invocation = catalog._CatalogInvocation(
                ComposerCatalogEntryKind.skill, "skill", "/new/path"
            )
            snapshot = catalog._CatalogSnapshot(entries, (("$skill", invocation),))
            for _ in range(300):
                await bench.child._publish_catalog()
            assert bench.child._catalog_snapshot.resolve("$skill") == invocation
            assert bench.sink.composer_catalog_reports == [(), entries]
            snapshot = catalog.EMPTY_CATALOG_SNAPSHOT
            await bench.child._publish_catalog()
            assert bench.sink.composer_catalog_reports == [(), entries, ()]

    _run(exercise)


@pytest.mark.parametrize("cancel", [False, True], ids=["sink-error", "sink-cancellation"])
def test_failed_publication_preserves_admitted_snapshot_and_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cancel: bool,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as bench:
            snapshot = catalog.EMPTY_CATALOG_SNAPSHOT
            monkeypatch.setattr(catalog, "_catalog_snapshot", lambda *args: snapshot)
            await bench.child._publish_catalog()
            snapshot = catalog._CatalogSnapshot(catalog.CODEX_BUILT_IN_CATALOG_ENTRIES)
            report = bench.sink.composer_catalog_reported

            async def fail(entries: Any) -> None:
                if cancel:
                    raise asyncio.CancelledError
                raise RuntimeError("sink failed")

            monkeypatch.setattr(bench.sink, "composer_catalog_reported", fail)
            with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
                await bench.child._publish_catalog()
            assert bench.child._catalog_snapshot == catalog.EMPTY_CATALOG_SNAPSHOT
            assert bench.child._published_catalog_entries == ()
            monkeypatch.setattr(bench.sink, "composer_catalog_reported", report)
            await bench.child._publish_catalog()
            assert bench.sink.composer_catalog_reports == [(), snapshot.entries]

    _run(exercise)


def test_each_child_starts_unpublished_and_keeps_its_own_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(
            catalog, "_catalog_snapshot", lambda *args: catalog.EMPTY_CATALOG_SNAPSHOT
        )
        for index in range(2):
            (tmp_path / str(index)).mkdir()
            async with _scripted_child(tmp_path / str(index), script={}) as bench:
                assert bench.child._published_catalog_entries is None
                await bench.child._publish_catalog()
                await bench.child._publish_catalog()
                assert bench.sink.composer_catalog_reports == [()]

    _run(exercise)


def test_source_hint_burst_during_read_is_one_more_pass_and_stop_drains_tasks() -> None:
    async def exercise() -> None:
        now = [0.0]
        entered, release = asyncio.Event(), asyncio.Event()
        reads = 0

        async def skills() -> bindings.SkillsListResponse:
            nonlocal reads
            reads += 1
            if reads == 1:
                entered.set()
                await release.wait()
            return bindings.SkillsListResponse(data=[])

        async def plugins() -> bindings.PluginInstalledResponse:
            return bindings.PluginInstalledResponse(marketplaces=[])

        async def installed() -> bindings.AppsInstalledResponse:
            return bindings.AppsInstalledResponse(apps=[])

        async def metadata(ids: list[str]) -> bindings.AppsReadResponse:
            return bindings.AppsReadResponse(apps=[], missingAppIds=ids)

        async def publish() -> None:
            return None

        coordinator = CatalogCoordinator(
            read_skills=skills,
            read_plugins=plugins,
            read_installed=installed,
            read_metadata=metadata,
            publish=publish,
            clock=lambda: now[0],
        )
        coordinator.start()
        await asyncio.wait_for(entered.wait(), 1)
        for _ in range(300):
            coordinator.hint_skills()
        release.set()
        now[0] = 1.0
        coordinator.wake()

        async def idle() -> None:
            while reads != 2 or any(lane.pending for lane in coordinator._lanes.values()):
                await asyncio.sleep(0)

        await asyncio.wait_for(idle(), 1)
        await asyncio.sleep(0)
        assert reads == 2
        tasks = tuple(lane.task for lane in coordinator._lanes.values())
        await coordinator.close()
        assert all(task is not None and task.done() for task in tasks)
        coordinator.hint_skills()
        coordinator.wake()
        await asyncio.sleep(0)
        assert reads == 2

    _run(exercise)
