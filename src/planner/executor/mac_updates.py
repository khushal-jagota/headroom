"""Apply the update target each lease reply carries, and prepare a runtime handoff.

The lease loop only offers the newest target; this controller applies it in its own
task, so a download or a slow stop never delays a lease. Reports and archives still use
the version-independent update routes. The ready release's runtime is downloaded and
extracted in the background before any update starts, so activation finds it verified
on disk and skips the download.
"""

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

from planner.environments.mac_runtime import extract_runtime, read_runtime_revision
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import EXECUTOR_ID_HEADER, ExecutorPaths
from planner.executor.mac_presence import MacPresence
from planner.executor.mac_runner import MacRunner
from planner.executor.runtime_bootstrap import atomic_record, read_record
from planner.executor.update_configuration import (
    MAC_RUNTIME_HANDOFF_ENV,
    MAC_RUNTIME_READY_ENV,
    MAC_UPDATE_POLL_SECONDS,
    MAC_UPDATE_SCHEMA_VERSION,
    MAX_MAC_RUNTIME_ARCHIVE_BYTES,
    MAX_MAC_UPDATE_DETAIL_CHARACTERS,
    MAX_MAC_UPDATE_RESPONSE_BYTES,
)
from planner.executor.update_contracts import (
    MacRuntimeSelection,
    MacUpdateCheck,
    MacUpdatePhase,
    MacUpdatePrefetch,
    MacUpdateReport,
    MacUpdateTarget,
    mac_prefetch_archive_path,
)


def handoff_identity(identity: ExecutorIdentity) -> ExecutorIdentity:
    """A normal launch gets a new incarnation. Only a planned swap inherits one.

    Protocol 22 never reads connection_generation. It keeps the value it found (0 if
    none) and writes it back unchanged into every handoff.json, so the previous runtime
    can still parse the record after a rollback.
    """
    path = os.environ.get(MAC_RUNTIME_HANDOFF_ENV)
    if path is None:
        return identity
    record = read_record(Path(path))
    if record is None:
        raise ValueError("planned update lacks its durable executor handoff")
    incarnation = record.get("executor_incarnation")
    generation = record.get("connection_generation", 0)
    if not isinstance(incarnation, str) or type(generation) is not int or generation < 0:
        raise ValueError("invalid planned executor handoff")
    UUID(incarnation)
    return replace(identity, executor_incarnation=incarnation, connection_generation=generation)


def handoff_record(identity: ExecutorIdentity, *, replay_pending: bool) -> dict[str, Any]:
    return {
        "executor_incarnation": identity.executor_incarnation,
        "connection_generation": identity.connection_generation,
        "replay_pending": replay_pending,
    }


class MacUpdateController:
    def __init__(
        self,
        *,
        paths: ExecutorPaths,
        runner: MacRunner,
        presence: MacPresence,
        server_url: str,
        executor_id: str,
        session_factory: Callable[[], aiohttp.ClientSession],
        request_exit: Callable[[], None],
    ) -> None:
        self.root = paths.root / "updates"
        self.runner, self.presence = runner, presence
        self.server_url, self.executor_id = server_url, executor_id
        self.session_factory, self.request_exit = session_factory, request_exit
        self.prepared_request: str | None = None
        self.confirmed_request: str | None = None
        self._runtime_gate_pending = (
            runner.planned_update_replay or os.environ.get(MAC_RUNTIME_READY_ENV) is not None
        )
        self._lock = asyncio.Lock()
        # The newest target a lease reply offered, applied by run() in its own task.
        self.latest: MacUpdateCheck | None = None
        self._offered = asyncio.Event()
        # One background download at a time, and one try per archive sha256 in a process.
        self._prefetch: tuple[str, asyncio.Task[None]] | None = None

    def offer(self, check: MacUpdateCheck) -> None:
        """Called by the lease loop; never waits. The newest check wins."""
        self.latest = check
        self._offered.set()

    async def _json(
        self,
        session: aiohttp.ClientSession,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        async with session.request(
            method,
            self.server_url + path,
            headers={EXECUTOR_ID_HEADER: self.executor_id},
            json=payload,
            allow_redirects=False,
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
        self,
        session: aiohttp.ClientSession,
        target: MacUpdateTarget,
        phase: MacUpdatePhase,
        detail: str | None = None,
    ) -> bool:
        report = MacUpdateReport(
            target.request_id,
            target.application_revision,
            phase,
            detail,
            self.presence.identity if phase in ("current", "ready_to_restart") else None,
        )
        response = await self._json(session, "POST", "/api/executor/update", asdict(report))
        return isinstance(response, dict) and response.get("confirmed") is True

    @staticmethod
    def _valid_runtime_reference(
        revision: str, archive_sha256: str, archive_size: int, archive_path: str, expected_path: str
    ) -> bool:
        return (
            re.fullmatch(r"[0-9a-f]{40}", revision) is not None
            and re.fullmatch(r"[0-9a-f]{64}", archive_sha256) is not None
            and archive_path == expected_path
            and 0 < archive_size <= MAX_MAC_RUNTIME_ARCHIVE_BYTES
        )

    def _release_path(self, revision: str, archive_sha256: str) -> Path:
        # extract_runtime renames a release into this name only after verifying its bytes.
        return self.root / "releases" / f"{revision}-{archive_sha256}"

    async def _fetch(
        self,
        session: aiohttp.ClientSession,
        archive_path: str,
        *,
        revision: str,
        archive_sha256: str,
        archive_size: int,
        download_name: str,
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        archive = self.root / download_name
        try:
            async with session.get(
                self.server_url + archive_path,
                headers={EXECUTOR_ID_HEADER: self.executor_id, "Accept-Encoding": "identity"},
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ConnectionError("Mac runtime archive is unavailable")
                if response.headers.get("Content-Encoding"):
                    # The session does not decode transport encoding, so encoded bytes
                    # would only fail the size and hash checks later, as ValueError.
                    raise ConnectionError("Mac runtime archive arrived with a transport encoding")
                size = 0
                with archive.open("wb") as stream:
                    async for block in response.content.iter_chunked(1024 * 1024):
                        size += len(block)
                        if size > archive_size:
                            raise ValueError("Mac archive exceeds its published size")
                        stream.write(block)
                    stream.flush()
                    os.fsync(stream.fileno())
            return await asyncio.to_thread(
                extract_runtime,
                archive,
                self.root / "releases",
                expected_sha256=archive_sha256,
                expected_size=archive_size,
                expected_revision=revision,
            )
        finally:
            archive.unlink(missing_ok=True)

    async def _download(self, session: aiohttp.ClientSession, target: MacUpdateTarget) -> Path:
        if re.fullmatch(r"[A-Za-z0-9_-]+", target.request_id) is None or not (
            self._valid_runtime_reference(
                target.application_revision,
                target.archive_sha256,
                target.archive_size,
                target.archive_path,
                f"/api/executor/update/{target.request_id}/archive",
            )
        ):
            raise ValueError("invalid Mac runtime target")
        if self._prefetch is not None and self._prefetch[0] == target.archive_sha256:
            # The same bytes are already on their way; a second download would race them.
            await asyncio.wait({self._prefetch[1]})
        release = self._release_path(target.application_revision, target.archive_sha256)
        if release.is_dir():
            read_runtime_revision(release, target.application_revision)
            return release
        return await self._fetch(
            session,
            target.archive_path,
            revision=target.application_revision,
            archive_sha256=target.archive_sha256,
            archive_size=target.archive_size,
            download_name="download.tar.gz",
        )

    def _start_prefetch(self, prefetch: MacUpdatePrefetch | None) -> None:
        """Download the ready runtime without stopping work or holding up the poll."""
        if (
            prefetch is None
            or prefetch.application_revision == self.presence.identity.application_revision
            or (
                self._prefetch is not None
                and (not self._prefetch[1].done() or self._prefetch[0] == prefetch.archive_sha256)
            )
            or not self._valid_runtime_reference(
                prefetch.application_revision,
                prefetch.archive_sha256,
                prefetch.archive_size,
                prefetch.archive_path,
                mac_prefetch_archive_path(prefetch.archive_sha256),
            )
            or self._release_path(prefetch.application_revision, prefetch.archive_sha256).exists()
        ):
            return

        async def download() -> None:
            # A failed prefetch only means activation downloads. Retrying the same bytes
            # every poll would load a working Mac with repeated full downloads.
            with contextlib.suppress(OSError, ValueError, ConnectionError, aiohttp.ClientError):
                async with self.session_factory() as session:
                    await self._fetch(
                        session,
                        prefetch.archive_path,
                        revision=prefetch.application_revision,
                        archive_sha256=prefetch.archive_sha256,
                        archive_size=prefetch.archive_size,
                        download_name="prefetch.tar.gz",
                    )

        self._prefetch = (prefetch.archive_sha256, asyncio.create_task(download()))

    async def apply(self, check: MacUpdateCheck) -> None:
        async with self._lock, self.session_factory() as session:
            self.release_handoff_custody()
            self.release_runtime_admission()
            if check.schema_version != MAC_UPDATE_SCHEMA_VERSION:
                raise ValueError("unsupported Mac update channel schema")
            self._start_prefetch(check.prefetch)
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
                    session,
                    target,
                    "failed" if failure.get("phase") == "failed" else "reverted",
                    str(failure.get("detail", "")),
                )
                return
            if target.application_revision == self.presence.identity.application_revision:
                if self.presence.leased() and self.confirmed_request != target.request_id:
                    if await self._report(session, target, "current"):
                        marker = os.environ.get(MAC_RUNTIME_READY_ENV)
                        if marker is not None:
                            atomic_record(
                                Path(marker),
                                {
                                    "request_id": target.request_id,
                                    "application_revision": target.application_revision,
                                    "identity": asdict(self.presence.identity),
                                },
                            )
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
                    atomic_record(
                        self.root / "failure.json",
                        {
                            "request_id": target.request_id,
                            "phase": "failed",
                            "detail": detail,
                            "admission_stopped": True,
                        },
                    )
                    await self._report(session, target, "failed", detail)
                    return
                if not prepared:
                    await self._report(session, target, "waiting")
                    return
                self.prepared_request = target.request_id
            if not target.activate or self.presence.leased():
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
                detail = f"Mac runtime installation failed: {type(error).__name__}: {error}"[
                    :MAX_MAC_UPDATE_DETAIL_CHARACTERS
                ]
                atomic_record(
                    self.root / "failure.json",
                    {
                        "request_id": target.request_id,
                        "phase": "failed",
                        "detail": detail,
                    },
                )
                self.prepared_request = None
                self._resume_if_safe()
                await self._report(session, target, "failed", detail)
                return
            previous = read_record(self.root / "selection.json")
            selection = MacRuntimeSelection(
                target.request_id,
                target.application_revision,
                str(runtime),
                str(previous["runtime_path"]) if previous else None,
                self.presence.identity.application_revision,
                self.presence.identity.executor_incarnation,
                self.presence.identity.connection_generation,
                target.archive_sha256,
            )
            atomic_record(
                self.root / "handoff.json",
                handoff_record(self.presence.identity, replay_pending=True),
            )
            atomic_record(self.root / "pending.json", asdict(selection))
            if self.presence.leased():
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
            not self._runtime_gate_pending
            or self.runner.planned_update_replay
            or self.prepared_request is not None
        ):
            return
        if os.environ.get(MAC_RUNTIME_READY_ENV) is not None:
            selected = read_record(self.root / "selection.json")
            if (
                self.confirmed_request is None
                or selected is None
                or selected.get("request_id") != self.confirmed_request
                or (self.root / "pending.json").exists()
            ):
                return
        self.runner.resume_after_cancelled_update()
        self._runtime_gate_pending = False

    def release_handoff_custody(self) -> None:
        path = os.environ.get(MAC_RUNTIME_HANDOFF_ENV)
        if path is None or not self.presence.leased() or self.runner.outbox.pending_count() != 0:
            return
        receipt = read_record(Path(path))
        if receipt is not None and receipt.get("replay_pending") is True:
            atomic_record(Path(path), handoff_record(self.presence.identity, replay_pending=False))
            self.runner.finish_planned_replay()
            self.release_runtime_admission()

    async def run(self) -> None:
        """Apply each offered target in turn, the newest first."""
        try:
            while True:
                if self.runner.planned_update_replay or self._runtime_gate_pending:
                    # While a planned handoff replays, custody is re-checked each second,
                    # so admission reopens as soon as the old output is receipted.
                    try:
                        await asyncio.wait_for(self._offered.wait(), MAC_UPDATE_POLL_SECONDS)
                    except TimeoutError:
                        self.release_handoff_custody()
                        self.release_runtime_admission()
                        continue
                else:
                    await self._offered.wait()
                self._offered.clear()
                check = self.latest
                if check is None:
                    continue
                try:
                    await self.apply(check)
                except (OSError, ValueError, ConnectionError, aiohttp.ClientError):
                    # A failed apply never authorizes activation; the next lease offers
                    # the target again.
                    pass
        finally:
            if self._prefetch is not None:
                self._prefetch[1].cancel()
