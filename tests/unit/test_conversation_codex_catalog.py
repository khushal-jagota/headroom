"""Catalog publication, finite refreshes, and the client's actual receive ordering.

The recording sink proves admission before any journal write. The real client router
proves response/notification races without scheduling guesses or a provider account.
The existing scripted child proves the same refresh loop across a subprocess pipe.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from tests.support.conversation_codex_app_server_bench import _run, _scripted_child

from planner.conversation.backends.codex_app_server import adapter as catalog
from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.client import CodexAppServerError
from planner.conversation.contracts import ComposerCatalogEntryKind


def _app(name: str) -> dict[str, Any]:
    return {"id": name, "name": name, "isAccessible": True, "isEnabled": True}


def _installed(*names: str) -> dict[str, Any]:
    return {"apps": [{"id": name, "enabled": True, "callable": True} for name in names]}


def _tokens(child: catalog.CodexAppServerBackendChild) -> set[str]:
    return {entry.insertion_text.strip() for entry in child._catalog_snapshot.entries}


async def _idle(child: catalog.CodexAppServerBackendChild) -> None:
    while child._catalog_refresh_task is not None:
        await child._catalog_refresh_task
    assert not child._catalog_work_pending()


def _wire(
    child: catalog.CodexAppServerBackendChild,
    monkeypatch: pytest.MonkeyPatch,
    app_response: Callable[[dict[str, Any]], Awaitable[None]],
    *,
    sources: dict[str, Any] | None = None,
) -> list[str]:
    """Use the real pending futures and router, with deterministic source replies."""
    child._thread_id = "thread"
    monkeypatch.setattr(child, "_bound_thread", lambda: "thread")
    data = {
        "skills/list": {"data": []},
        "app/installed": _installed("old", "new", "B"),
        "plugin/installed": {"marketplaces": []},
        **(sources or {}),
    }
    reads: list[str] = []

    async def write(message: Any) -> None:
        method = message["method"]
        reads.append(method)
        if method == "app/list":
            await app_response(message)
        else:
            await child._client._route({"id": message["id"], "result": data[method]})

    monkeypatch.setattr(child._client, "_write", write)
    return reads


async def _notify(child: catalog.CodexAppServerBackendChild, *names: str) -> None:
    await child._client._route({
        "method": "app/list/updated", "params": {"data": [_app(name) for name in names]},
    })


def test_publication_tracks_entries_but_installs_equal_fresh_invocations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child
            snapshot = catalog.EMPTY_CATALOG_SNAPSHOT

            async def read() -> catalog._CatalogSnapshot:
                return snapshot

            monkeypatch.setattr(child, "_read_catalog_snapshot", read)
            await child._refresh_catalog()
            await child._refresh_catalog()
            assert bench.sink.composer_catalog_reports == [()]
            entries = catalog.CODEX_BUILT_IN_CATALOG_ENTRIES
            snapshot = catalog._CatalogSnapshot(entries)
            await child._refresh_catalog()
            invocation = catalog._CatalogInvocation(
                ComposerCatalogEntryKind.skill, "skill", "/new/path"
            )
            snapshot = catalog._CatalogSnapshot(entries, (("$skill", invocation),))
            for _ in range(300):
                await child._refresh_catalog()
            assert child._catalog_snapshot.resolve("$skill") == invocation
            assert bench.sink.composer_catalog_reports == [(), entries]
            snapshot = catalog.EMPTY_CATALOG_SNAPSHOT
            await child._refresh_catalog()
            assert bench.sink.composer_catalog_reports == [(), entries, ()]

    _run(exercise)


@pytest.mark.parametrize("cancel", [False, True], ids=["sink-error", "sink-cancellation"])
def test_failed_publication_preserves_snapshot_and_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child
            snapshot = catalog.EMPTY_CATALOG_SNAPSHOT

            async def read() -> catalog._CatalogSnapshot:
                return snapshot

            monkeypatch.setattr(child, "_read_catalog_snapshot", read)
            await child._refresh_catalog()
            snapshot = catalog._CatalogSnapshot(catalog.CODEX_BUILT_IN_CATALOG_ENTRIES)
            report = bench.sink.composer_catalog_reported

            async def fail(entries: Any) -> None:
                if cancel:
                    raise asyncio.CancelledError
                raise RuntimeError("sink failed")

            monkeypatch.setattr(bench.sink, "composer_catalog_reported", fail)
            with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
                await child._refresh_catalog()
            assert child._catalog_snapshot == catalog.EMPTY_CATALOG_SNAPSHOT
            assert child._published_catalog_entries == ()
            monkeypatch.setattr(bench.sink, "composer_catalog_reported", report)
            await child._refresh_catalog()
            assert bench.sink.composer_catalog_reports == [(), snapshot.entries]

    _run(exercise)


@pytest.mark.parametrize("after", [False, True], ids=["notification-before", "notification-after"])
@pytest.mark.parametrize("response_kind", ["success", "error", "malformed", "cursor-loop"])
def test_receive_order_and_persistent_winner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after: bool, response_kind: str,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(catalog, "CATALOG_REFRESH_COALESCE_SECONDS", 0)
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child
            page = 0

            async def respond(message: dict[str, Any]) -> None:
                nonlocal page
                page += 1
                final = response_kind != "cursor-loop" or page == 2
                if final and not after:
                    await _notify(child, "new")
                if response_kind == "error":
                    response = {"error": {"code": -1, "message": "apps failed"}}
                elif response_kind == "malformed":
                    response = {"result": {"data": ["invalid"]}}
                else:
                    response = {"result": {"data": [_app("old")]}}
                    if response_kind == "cursor-loop":
                        response["result"]["nextCursor"] = "repeated"
                await child._client._route({"id": message["id"], **response})
                if final and after:
                    await _notify(child, "new")

            reads = _wire(child, monkeypatch, respond)
            await child._refresh_catalog()
            await _idle(child)
            tokens = _tokens(child)
            if after:
                assert "@new" in tokens
                assert "@old" not in tokens
            elif response_kind == "success":
                assert "@old" in tokens
                assert "@new" not in tokens
            else:
                assert not {"@new", "@old"} & tokens
            # The pending app pass cannot resurrect the older notification or query again.
            assert reads.count("app/list") == (2 if response_kind == "cursor-loop" else 1)
            assert reads.count("app/installed") == 2
            assert len(bench.sink.composer_catalog_reports) == 1

    _run(exercise)


def test_success_uses_first_page_freshness_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(catalog, "CATALOG_REFRESH_COALESCE_SECONDS", 0)
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child
            page = 0

            async def respond(message: dict[str, Any]) -> None:
                nonlocal page
                page += 1
                result = {"data": [_app("old")], "nextCursor": "second"} if page == 1 else {
                    "data": [_app("B")],
                }
                await child._client._route({"id": message["id"], "result": result})
                if page == 1:
                    await _notify(child, "new", "B")

            _wire(child, monkeypatch, respond)
            await child._refresh_catalog()
            await _idle(child)
            assert {"@new", "@b"} <= _tokens(child)
            assert "@old" not in _tokens(child)

    _run(exercise)


@pytest.mark.parametrize("recovery", [False, True], ids=["before-timeout", "after-timeout"])
def test_timeout_ordinal_is_recorded_before_adapter_resumption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recovery: bool,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(catalog, "CATALOG_REQUEST_TIMEOUT_SECONDS", 0.01)
        monkeypatch.setattr(catalog, "CATALOG_REFRESH_COALESCE_SECONDS", 0)
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child

            async def no_response(message: dict[str, Any]) -> None:
                await _notify(child, "old")

            _wire(child, monkeypatch, no_response)
            original = child._client._catalog_app_list

            async def wait_then_recover(params: Any, *, timeout_seconds: float) -> tuple[Any, int]:
                try:
                    return await original(params, timeout_seconds=timeout_seconds)
                except CodexAppServerError:
                    if recovery:
                        # Recovery reaches the real router before the adapter catches failure.
                        await _notify(child, "new")
                    raise

            monkeypatch.setattr(child._client, "_catalog_app_list", wait_then_recover)
            await child._refresh_catalog()
            await _idle(child)
            assert ("@new" in _tokens(child)) is recovery
            assert "@old" not in _tokens(child)
            assert child._client._wire_broken is False

    _run(exercise)


def test_wire_failure_ordinal_precedes_recovery_notification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(catalog, "CATALOG_REFRESH_COALESCE_SECONDS", 0)
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child

            async def fail_pending(message: dict[str, Any]) -> None:
                child._client._fail_everything_pending("wire lost")
                await _notify(child, "new")

            _wire(child, monkeypatch, fail_pending)
            await child._refresh_catalog()
            await _idle(child)
            assert "@new" in _tokens(child)

    _run(exercise)


def test_source_degradation_is_truthful_and_recovery_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child
            unavailable = False

            async def respond(message: dict[str, Any]) -> None:
                envelope: dict[str, Any] = {
                    "error": {"message": "apps unavailable"}
                } if unavailable else {"result": {"data": [_app("old")]}}
                await child._client._route({"id": message["id"], **envelope})

            _wire(child, monkeypatch, respond)
            await child._refresh_catalog()
            assert "@old" in _tokens(child)
            unavailable = True
            await child._refresh_catalog()
            await child._refresh_catalog()
            assert "@old" not in _tokens(child)
            assert "/compact" in _tokens(child)
            assert len(bench.sink.composer_catalog_reports) == 2
            unavailable = False
            await child._refresh_catalog()
            assert "@old" in _tokens(child)
            assert len(bench.sink.composer_catalog_reports) == 3

    _run(exercise)


@pytest.mark.parametrize("skill", [False, True], ids=["changed-app", "skill-invalidation"])
def test_input_during_sink_preserves_generations_and_changed_callability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, skill: bool,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(catalog, "CATALOG_REFRESH_COALESCE_SECONDS", 0)
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child

            async def respond(message: dict[str, Any]) -> None:
                await child._client._route({"id": message["id"], "result": {"data": [_app("old")]}})

            sources = {"app/installed": _installed("old", "new")}
            reads = _wire(child, monkeypatch, respond, sources=sources)
            arrived = asyncio.Event()
            release = asyncio.Event()
            report = bench.sink.composer_catalog_reported

            async def blocked_report(entries: Any) -> None:
                arrived.set()
                await release.wait()
                await report(entries)

            monkeypatch.setattr(bench.sink, "composer_catalog_reported", blocked_report)
            refresh = asyncio.create_task(child._refresh_catalog())
            await arrived.wait()
            current = "old" if skill else "new"
            await _notify(child, current)
            if skill:
                await child._client._route({"method": "skills/changed", "params": {}})
            release.set()
            await refresh
            await _idle(child)
            expected_queries = 2 if skill else 1
            expected_reports = 1 if skill else 2
            assert reads.count("app/list") == expected_queries
            assert len(bench.sink.composer_catalog_reports) == expected_reports
            assert f"@{current}" in _tokens(child)
            # Equal app metadata still forces the sibling reads that establish callability.
            original_read = child._read_installed_apps

            async def no_installed(thread_id: str) -> bindings.AppsInstalledResponse:
                await original_read(thread_id)
                return bindings.AppsInstalledResponse(apps=[])

            monkeypatch.setattr(child, "_read_installed_apps", no_installed)
            await _notify(child, current)
            await _idle(child)
            assert f"@{current}" not in _tokens(child)
            assert reads.count("app/list") == expected_queries
            assert len(bench.sink.composer_catalog_reports) == expected_reports + 1

    _run(exercise)


def test_scripted_pages_and_repeated_notifications_drain_without_app_list_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(catalog, "CATALOG_REFRESH_COALESCE_SECONDS", 0.001)
        async with _scripted_child(tmp_path, script={
            "installed_apps": _installed("old", "new", "B"),
            "app_list_steps": [
                {"result": {"data": [_app("old")], "nextCursor": "second"},
                 "after": [[_app("new"), _app("B")]] * 20},
                {"result": {"data": [_app("B")]},
                 "after": [[_app("new"), _app("B")]] * 20},
            ],
        }) as bench:
            await bench.start(cursor=None)
            await _idle(bench.child)
            assert len(bench.all_sent("app/list")) == 2
            assert {"@new", "@b"} <= _tokens(bench.child)
            assert "@old" not in _tokens(bench.child)
            assert len(bench.sink.composer_catalog_reports) == 1
            assert len(bench.all_sent("app/installed")) <= 3

    _run(exercise)


def test_each_pending_pass_coalesces_and_stop_cancels_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        interval = 0.01
        monkeypatch.setattr(catalog, "CATALOG_REFRESH_COALESCE_SECONDS", interval)
        async with _scripted_child(tmp_path, script={}) as bench:
            child = bench.child
            passes: list[float] = []
            pending_read = asyncio.Event()

            async def read(*, query_apps: bool = True) -> catalog._CatalogSnapshot:
                passes.append(asyncio.get_running_loop().time())
                if len(passes) == 1:
                    child._request_catalog_refresh()
                if len(passes) == 3:
                    pending_read.set()
                    await asyncio.Event().wait()
                return catalog.EMPTY_CATALOG_SNAPSHOT

            monkeypatch.setattr(child, "_read_catalog_snapshot", read)
            child._request_catalog_refresh()
            await _idle(child)
            assert len(passes) == 2
            assert passes[1] - passes[0] >= interval * 0.9
            child._request_catalog_refresh()
            await pending_read.wait()
            runner = child._catalog_refresh_task
            await child.stop()
            assert runner is not None and runner.cancelled()
            assert child._catalog_refresh_task is None

    _run(exercise)


def test_new_child_starts_unpublished_and_existing_child_keeps_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        for directory in (tmp_path / "first", tmp_path / "new"):
            directory.mkdir()
            async with _scripted_child(directory, script={}) as bench:
                child = bench.child

                async def read() -> catalog._CatalogSnapshot:
                    return catalog.EMPTY_CATALOG_SNAPSHOT

                monkeypatch.setattr(child, "_read_catalog_snapshot", read)
                assert child._published_catalog_entries is None
                await child._refresh_catalog()
                # A surviving child reuses the same adapter across relay reconnect.
                await child._refresh_catalog()
                assert bench.sink.composer_catalog_reports == [()]

    _run(exercise)
