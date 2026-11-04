"""Independent recovery polling and durable preparation for a runtime handoff."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import UUID

import aiohttp
from pydantic import TypeAdapter

from planner.environments.mac_runtime import extract_runtime
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import EXECUTOR_ID_HEADER, ExecutorPaths
from planner.executor.mac_connection import MacConnection
from planner.executor.mac_runner import MacRunner
from planner.executor.runtime_bootstrap import atomic_record, read_record
from planner.executor.update_configuration import (
    MAC_RUNTIME_HANDOFF_ENV,
    MAC_RUNTIME_READY_ENV,
    MAC_UPDATE_POLL_SECONDS,
    MAC_UPDATE_SCHEMA_VERSION,
    MAX_MAC_RUNTIME_ARCHIVE_BYTES,
    MAX_MAC_UPDATE_RESPONSE_BYTES,
)
from planner.executor.update_contracts import (
    MacRuntimeSelection,
    MacUpdateCheck,
    MacUpdatePhase,
    MacUpdateReport,
    MacUpdateTarget,
)


def handoff_identity(identity: ExecutorIdentity) -> ExecutorIdentity:
    """A normal launch gets a new incarnation. Only a planned swap inherits one."""
    path = os.environ.get(MAC_RUNTIME_HANDOFF_ENV)
    if path is None:
        return identity
    record = read_record(Path(path))
    if record is None:
        raise ValueError("planned update lacks its durable executor handoff")
    incarnation = record.get("executor_incarnation")
    generation = record.get("connection_generation")
    if not isinstance(incarnation, str) or type(generation) is not int or generation < 0:
        raise ValueError("invalid planned executor handoff")
    UUID(incarnation)
    return replace(identity, executor_incarnation=incarnation, connection_generation=generation)


def advance_generation(identity: ExecutorIdentity) -> ExecutorIdentity:
    advanced = replace(identity, connection_generation=identity.connection_generation + 1)
    path = os.environ.get(MAC_RUNTIME_HANDOFF_ENV)
    if path is not None:
        previous = read_record(Path(path)) or {}
        atomic_record(Path(path), {
            "executor_incarnation": advanced.executor_incarnation,
            "connection_generation": advanced.connection_generation,
            "replay_pending": previous.get("replay_pending", True),
        })
    return advanced


class MacUpdateController:
    def __init__(
        self,
        *,
        paths: ExecutorPaths,
        runner: MacRunner,
        connection: MacConnection,
        server_url: str,
        executor_id: str,
        session_factory: Callable[[], aiohttp.ClientSession],
        request_exit: Callable[[], None],
    ) -> None:
        self.root = paths.root / "updates"
        self.runner, self.connection = runner, connection
        self.server_url, self.executor_id = server_url, executor_id
        self.session_factory, self.request_exit = session_factory, request_exit
        self.prepared_request: str | None = None
        self.confirmed_request: str | None = None
        self._runtime_gate_pending = (
            runner.planned_update_replay or os.environ.get(MAC_RUNTIME_READY_ENV) is not None
        )
        self._lock = asyncio.Lock()

    async def _json(
        self, session: aiohttp.ClientSession, method: str, path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        async with session.request(
            method, self.server_url + path,
            headers={EXECUTOR_ID_HEADER: self.executor_id},
            json=payload, allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ConnectionError("Mac update channel refused the request")
            content = bytearray()
            async for block in response.content.iter_chunked(MAX_MAC_UPDATE_RESPONSE_BYTES + 1):
                content.extend(block)
                if len(content) > MAX_MAC_UPDATE_RESPONSE_BYTES:
                    raise ValueError("Mac update response exceeds its size bound")
            return json.loads(content)

    async def _report(
        self, session: aiohttp.ClientSession, target: MacUpdateTarget,
        phase: MacUpdatePhase, detail: str | None = None,
    ) -> bool:
        report = MacUpdateReport(
            target.request_id, target.application_revision,
            phase, detail,
            self.connection.identity if phase in ("current", "ready_to_restart") else None,
        )
        response = await self._json(session, "POST", "/api/executor/update", asdict(report))
        return isinstance(response, dict) and response.get("confirmed") is True

    async def _download(self, session: aiohttp.ClientSession, target: MacUpdateTarget) -> Path:
        if (
            re.fullmatch(r"[0-9a-f]{40}", target.application_revision) is None
            or re.fullmatch(r"[0-9a-f]{64}", target.archive_sha256) is None
            or re.fullmatch(r"[A-Za-z0-9_-]+", target.request_id) is None
            or target.archive_path != f"/api/executor/update/{target.request_id}/archive"
            or not 0 < target.archive_size <= MAX_MAC_RUNTIME_ARCHIVE_BYTES
        ):
            raise ValueError("invalid approved Mac runtime target")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive = self.root / "download.tar.gz"
        try:
            async with session.get(
                self.server_url + target.archive_path,
                headers={EXECUTOR_ID_HEADER: self.executor_id}, allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ConnectionError("approved Mac archive is unavailable")
                size = 0
                with archive.open("wb") as stream:
                    async for block in response.content.iter_chunked(1024 * 1024):
                        size += len(block)
                        if size > target.archive_size:
                            raise ValueError("Mac archive exceeds its approved size")
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
            return await asyncio.to_thread(
                extract_runtime, archive, self.root / "releases",
                expected_sha256=target.archive_sha256, expected_size=target.archive_size,
                expected_revision=target.application_revision,
            )
        finally:
            archive.unlink(missing_ok=True)

    async def check(self) -> None:
        async with self._lock, self.session_factory() as session:
            self.release_handoff_custody()
            self.release_runtime_admission()
            check = TypeAdapter(MacUpdateCheck).validate_python(
                await self._json(session, "GET", "/api/executor/update")
            )
            if check.schema_version != MAC_UPDATE_SCHEMA_VERSION:
                raise ValueError("unsupported Mac update channel schema")
            target = check.target
            if target is None:
                if self.prepared_request is not None:
                    self._resume_if_safe()
                    self.prepared_request = None
                return
            if self.prepared_request is not None and self.prepared_request != target.request_id:
                # Cancellation can reveal the last applied target rather than
                # null. That fallback must also release the canceled admission gate.
                self.prepared_request = None
                self._resume_if_safe()
            failure = read_record(self.root / "failure.json")
            if failure is not None and failure.get("request_id") == target.request_id:
                await self._report(
                    session, target, "failed" if failure.get("phase") == "failed" else "reverted",
                    str(failure.get("detail", "")),
                )
                return
            if target.application_revision == self.connection.identity.application_revision:
                if (
                    self.connection.ready_link() is not None
                    and self.confirmed_request != target.request_id
                ):
                    if await self._report(session, target, "current"):
                        marker = os.environ.get(MAC_RUNTIME_READY_ENV)
                        if marker is not None:
                            atomic_record(Path(marker), {
                                "request_id": target.request_id,
                                "application_revision": target.application_revision,
                                "identity": asdict(self.connection.identity),
                            })
                        self.confirmed_request = target.request_id
                        self.release_runtime_admission()
                return
            if self.runner.planned_update_replay:
                # A synthetic retirement can wait for the old FIFO. Joining it
                # in prepare_update would prevent the next custody-release poll.
                await self._report(session, target, "waiting")
                return
            if self.prepared_request != target.request_id:
                try:
                    prepared = await self.runner.prepare_update(target.mode)
                except (OSError, ValueError, RuntimeError) as error:
                    detail = f"Mac work could not stop safely: {type(error).__name__}."
                    atomic_record(self.root / "failure.json", {
                        "request_id": target.request_id, "phase": "failed", "detail": detail,
                        "admission_stopped": True,
                    })
                    await self._report(session, target, "failed", detail)
                    return
                if not prepared:
                    await self._report(session, target, "waiting")
                    return
                self.prepared_request = target.request_id
            if not target.activate or self.connection.ready_link() is not None:
                await self._report(session, target, "ready_to_restart")
            if not target.activate:
                return
            # A Mac without the fixed bootstrap cannot safely swap itself.
            # Every service launched through the installed entrypoint has it.
            from planner.executor.update_configuration import MAC_RUNTIME_CHILD_ENV

            if os.environ.get(MAC_RUNTIME_CHILD_ENV) != "1":
                raise ValueError("Mac runtime update requires the fixed supervisor")
            await self._report(session, target, "downloading")
            try:
                runtime = await self._download(session, target)
            except (OSError, ValueError, ConnectionError, aiohttp.ClientError) as error:
                detail = f"Mac runtime installation failed: {type(error).__name__}."
                atomic_record(self.root / "failure.json", {
                    "request_id": target.request_id, "phase": "failed", "detail": detail,
                })
                self.prepared_request = None
                self._resume_if_safe()
                await self._report(session, target, "failed", detail)
                return
            previous = read_record(self.root / "selection.json")
            selection = MacRuntimeSelection(
                target.request_id, target.application_revision, str(runtime),
                str(previous["runtime_path"]) if previous else None,
                self.connection.identity.application_revision,
                self.connection.identity.executor_incarnation,
                self.connection.identity.connection_generation,
                target.archive_sha256,
            )
            atomic_record(self.root / "handoff.json", {
                "executor_incarnation": selection.executor_incarnation,
                "connection_generation": selection.connection_generation,
                "replay_pending": True,
            })
            atomic_record(self.root / "pending.json", asdict(selection))
            if self.connection.ready_link() is not None:
                await self._report(session, target, "ready_to_restart")
            self.request_exit()

    def _resume_if_safe(self) -> None:
        path = os.environ.get(MAC_RUNTIME_HANDOFF_ENV)
        custody = read_record(Path(path)) if path is not None else None
        if custody is None or custody.get("replay_pending") is not True:
            if self._runtime_gate_pending:
                self.release_runtime_admission()
            else:
                self.runner.resume_after_cancelled_update()

    def release_runtime_admission(self) -> None:
        if (
            not self._runtime_gate_pending or self.runner.planned_update_replay
            or self.prepared_request is not None
        ):
            return
        if os.environ.get(MAC_RUNTIME_READY_ENV) is not None:
            selected = read_record(self.root / "selection.json")
            if (
                self.confirmed_request is None or selected is None
                or selected.get("request_id") != self.confirmed_request
                or (self.root / "pending.json").exists()
            ):
                return
        self.runner.resume_after_cancelled_update()
        self._runtime_gate_pending = False

    def release_handoff_custody(self) -> None:
        path = os.environ.get(MAC_RUNTIME_HANDOFF_ENV)
        if (
            path is None or self.connection.ready_link() is None
            or self.runner.outbox.pending_count() != 0
        ):
            return
        receipt = read_record(Path(path))
        if receipt is not None and receipt.get("replay_pending") is True:
            atomic_record(Path(path), {
                "executor_incarnation": self.connection.identity.executor_incarnation,
                "connection_generation": self.connection.identity.connection_generation,
                "replay_pending": False,
            })
            self.runner.finish_planned_replay()
            self.release_runtime_admission()

    async def poll(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.check()
            except (OSError, ValueError, ConnectionError, aiohttp.ClientError):
                # The same recovery channel remains available after a work
                # handshake refusal. A failed check never authorizes activation.
                pass
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), MAC_UPDATE_POLL_SECONDS)
