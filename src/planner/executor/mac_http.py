"""The Mac's HTTPS client for the executor routes (protocol 22).

Each loop has its own aiohttp session and connector, so a slow item batch never holds
up the lease, and a long poll never holds up a result report. Every request carries the
executor's identity headers, refuses redirects, ignores proxy environment variables and
has its route's own timeout. Nothing here retries: each loop owns its own backoff.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import re
import ssl
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final, Literal, cast

import aiohttp

from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import (
    COMMAND_OUTCOME_TIMEOUT_SECONDS,
    COMMAND_POLL_TIMEOUT_SECONDS,
    EXECUTOR_COMMAND_FILE_PATH,
    EXECUTOR_COMMAND_OUTCOMES_PATH,
    EXECUTOR_COMMANDS_PATH,
    EXECUTOR_FILE_SHA256_HEADER,
    EXECUTOR_FILE_UPLOAD_HEADER,
    EXECUTOR_ID_HEADER,
    EXECUTOR_INCARNATION_HEADER,
    EXECUTOR_ITEMS_PATH,
    EXECUTOR_LEASE_PATH,
    EXECUTOR_LIVE_PATH,
    EXECUTOR_PROTOCOL_HEADER,
    EXECUTOR_READS_PATH,
    EXECUTOR_UPLOAD_PATH,
    EXECUTOR_WORKSPACE_RETIREMENT_PATH,
    FILE_TRANSFER_STALL_SECONDS,
    HTTP_CLOSE_TIMEOUT_SECONDS,
    HTTP_CONNECT_TIMEOUT_SECONDS,
    ITEM_BATCH_TIMEOUT_SECONDS,
    LEASE_TIMEOUT_SECONDS,
    LIVE_EVENT_TIMEOUT_SECONDS,
    LIVE_READ_TIMEOUT_SECONDS,
    MAC_DOWNLOADS_CONNECTOR,
    MAC_ITEMS_CONNECTOR,
    MAC_LEASE_CONNECTOR,
    MAC_LIVE_CONNECTOR,
    MAC_OUTCOME_CONNECTOR,
    MAC_POLL_CONNECTOR,
    MAC_READS_CONNECTOR,
    MAX_COMMAND_OUTCOME_REPORT_BYTES,
    MAX_ENVELOPE_BYTES,
    MAX_ITEM_BATCH_BYTES,
    MAX_MAILBOX_POLL_BYTES,
    REQUEST_TIMEOUT_SECONDS,
    TRANSFER_CHUNK_BYTES,
    MacHttpConnectorSettings,
)
from planner.executor.files import regular_descriptor
from planner.executor.protocol import (
    CommandOutcomeReply,
    CommandOutcomeReport,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    JsonObject,
    LeaseRefused,
    LeaseReply,
    LeaseRequest,
    LiveEventBatch,
    LiveReadRequest,
    MailboxPollReply,
    WorkspaceRetirementReply,
    WorkspaceRetirementRequest,
    decode_bounded,
    encode_bounded,
)
from planner.ticket_workspaces.contracts import WorkspaceState

type MacLoop = Literal["lease", "poll", "outcomes", "items", "downloads", "reads", "live"]

_CONNECTORS: Final[dict[MacLoop, MacHttpConnectorSettings]] = {
    "lease": MAC_LEASE_CONNECTOR,
    "poll": MAC_POLL_CONNECTOR,
    "outcomes": MAC_OUTCOME_CONNECTOR,
    "items": MAC_ITEMS_CONNECTOR,
    "downloads": MAC_DOWNLOADS_CONNECTOR,
    "reads": MAC_READS_CONNECTOR,
    "live": MAC_LIVE_CONNECTOR,
}
_SAFE_CODE = re.compile(r"[a-z0-9_.-]{1,64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ExecutorHttpError(Exception):
    """A route answered other than 2xx. code is the error body's code, or "unknown"."""

    def __init__(self, status: int, code: str, message: str = "") -> None:
        super().__init__(f"executor route answered {status} {code}")
        self.status, self.code, self.message = status, code, message


def failure_name(error: BaseException) -> str:
    """Name a failure for a log line without its message text, which can carry payload
    content: the class, an HTTP status and code, and the cause's class."""
    if isinstance(error, ExecutorHttpError):
        name = f"HTTP {error.status} {error.code}"
    else:
        name = type(error).__name__
    if error.__cause__ is not None:
        name += f" from {type(error.__cause__).__name__}"
    return name


def _error(status: int, body: bytes) -> ExecutorHttpError:
    code, message = "unknown", ""
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        raw = error.get("code")
        if isinstance(raw, str) and _SAFE_CODE.fullmatch(raw):
            code = raw
        raw_message = error.get("message")
        if isinstance(raw_message, str):
            message = raw_message[:500]
    return ExecutorHttpError(status, code, message)


async def _read_bounded(response: aiohttp.ClientResponse, limit: int) -> bytes:
    content = bytearray()
    async for block in response.content.iter_chunked(64 * 1024):
        content.extend(block)
        if len(content) > limit:
            raise ValueError("executor response exceeds its size bound")
    return bytes(content)


def create_mac_session(
    settings: MacHttpConnectorSettings, ssl_context: ssl.SSLContext | None
) -> aiohttp.ClientSession:
    """One loop's session. Proxy variables and cookies are ignored, and bodies are never
    decompressed, so a size bound counts the bytes that actually arrived."""
    verify: ssl.SSLContext | bool = ssl_context if ssl_context is not None else True
    # aiohttp refuses a keepalive timeout alongside force_close, so it is set only when
    # connections are kept.
    connector = (
        aiohttp.TCPConnector(ssl=verify, limit=settings.limit, force_close=True)
        if settings.force_close
        else aiohttp.TCPConnector(
            ssl=verify,
            limit=settings.limit,
            keepalive_timeout=settings.keepalive_timeout,
        )
    )
    return aiohttp.ClientSession(
        connector=connector,
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        auto_decompress=False,
        timeout=aiohttp.ClientTimeout(total=None, sock_connect=HTTP_CONNECT_TIMEOUT_SECONDS),
    )


def create_update_session(ssl_context: ssl.SSLContext | None) -> aiohttp.ClientSession:
    """A session for one update check's reports and archive downloads (route 9)."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl_context if ssl_context is not None else True),
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        auto_decompress=False,
        timeout=aiohttp.ClientTimeout(
            total=None, sock_connect=HTTP_CONNECT_TIMEOUT_SECONDS, sock_read=REQUEST_TIMEOUT_SECONDS
        ),
    )


class MacHttpClient:
    """Implements executor.contracts.MacHttpClient. server_url is a validated origin."""

    def __init__(
        self,
        server_url: str,
        executor_id: str,
        identity: ExecutorIdentity,
        *,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.ssl_context = ssl_context
        self._headers = {
            EXECUTOR_ID_HEADER: executor_id,
            EXECUTOR_INCARNATION_HEADER: identity.executor_incarnation,
            EXECUTOR_PROTOCOL_HEADER: str(identity.protocol_version),
            # Bodies are never decompressed (auto_decompress=False), so none may be
            # compressed: the VPS gzips any answer over 1 KB that a client accepts gzipped.
            "Accept-Encoding": "identity",
        }
        self._sessions: dict[MacLoop, aiohttp.ClientSession] = {}

    def _session(self, loop: MacLoop) -> aiohttp.ClientSession:
        session = self._sessions.get(loop)
        if session is None or session.closed:
            session = create_mac_session(_CONNECTORS[loop], self.ssl_context)
            self._sessions[loop] = session
        return session

    async def _json(
        self,
        loop: MacLoop,
        method: str,
        path: str,
        *,
        timeout: float,
        reply_limit: int = MAX_ENVELOPE_BYTES,
        body: bytes | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        headers = dict(self._headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        async with self._session(loop).request(
            method,
            self.server_url + path,
            data=body,
            params=params,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=timeout, sock_connect=HTTP_CONNECT_TIMEOUT_SECONDS),
        ) as response:
            return response.status, await _read_bounded(response, reply_limit)

    async def lease(self, request: LeaseRequest) -> LeaseReply | LeaseRefused:
        status, body = await self._json(
            "lease",
            "POST",
            EXECUTOR_LEASE_PATH,
            timeout=LEASE_TIMEOUT_SECONDS,
            body=encode_bounded(request),
        )
        if status == 200:
            return decode_bounded(body, LeaseReply)
        if status == 409:
            try:
                return decode_bounded(body, LeaseRefused)
            except ValueError:
                pass
        raise _error(status, body)

    async def retire_workspace(self, state: WorkspaceState, *, replay: bool = False) -> bool:
        status, body = await self._json(
            "reads",
            "POST",
            EXECUTOR_WORKSPACE_RETIREMENT_PATH,
            timeout=LIVE_READ_TIMEOUT_SECONDS,
            body=encode_bounded(WorkspaceRetirementRequest(state, replay)),
        )
        if status != 200:
            raise _error(status, body)
        return decode_bounded(body, WorkspaceRetirementReply).authorized

    async def poll_commands(self, after: int, wait_seconds: float) -> MailboxPollReply:
        status, body = await self._json(
            "poll",
            "GET",
            EXECUTOR_COMMANDS_PATH,
            timeout=COMMAND_POLL_TIMEOUT_SECONDS,
            reply_limit=MAX_MAILBOX_POLL_BYTES,
            params={"after": str(after), "wait": str(int(wait_seconds))},
        )
        if status != 200:
            raise _error(status, body)
        return decode_bounded(body, MailboxPollReply, limit=MAX_MAILBOX_POLL_BYTES)

    async def report_outcomes(self, report: CommandOutcomeReport) -> CommandOutcomeReply:
        status, body = await self._json(
            "outcomes",
            "POST",
            EXECUTOR_COMMAND_OUTCOMES_PATH,
            timeout=COMMAND_OUTCOME_TIMEOUT_SECONDS,
            body=encode_bounded(report, limit=MAX_COMMAND_OUTCOME_REPORT_BYTES),
        )
        if status != 200:
            raise _error(status, body)
        return decode_bounded(body, CommandOutcomeReply)

    async def post_items(self, conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
        status, body = await self._json(
            "items",
            "POST",
            EXECUTOR_ITEMS_PATH.format(conversation_id=conversation_id),
            timeout=ITEM_BATCH_TIMEOUT_SECONDS,
            reply_limit=MAX_ITEM_BATCH_BYTES,
            body=encode_bounded(batch, limit=MAX_ITEM_BATCH_BYTES),
        )
        if status != 200:
            raise _error(status, body)
        return decode_bounded(body, ItemBatchReply, limit=MAX_ITEM_BATCH_BYTES)

    async def read(self, request: LiveReadRequest) -> JsonObject:
        status, body = await self._json(
            "reads",
            "POST",
            EXECUTOR_READS_PATH,
            timeout=LIVE_READ_TIMEOUT_SECONDS,
            body=encode_bounded(request),
        )
        if status != 200:
            raise _error(status, body)
        return cast(JsonObject, decode_bounded(body, dict))

    async def post_live(self, conversation_id: str, batch: LiveEventBatch) -> None:
        status, body = await self._json(
            "live",
            "POST",
            EXECUTOR_LIVE_PATH.format(conversation_id=conversation_id),
            timeout=LIVE_EVENT_TIMEOUT_SECONDS,
            body=encode_bounded(batch),
        )
        if status != 204:
            raise _error(status, body)

    async def upload_file(self, upload: FileUploadRequest, source: Path) -> None:
        """PUT the exact manifest bytes. No bytes moving for the stall limit fails it."""
        manifest = upload.manifest
        if not _SHA256.fullmatch(manifest.sha256) or manifest.byte_count < 0:
            raise ValueError("invalid upload manifest")
        described = base64.urlsafe_b64encode(encode_bounded(upload)).decode().rstrip("=")
        loop = asyncio.get_running_loop()
        async with asyncio.timeout(FILE_TRANSFER_STALL_SECONDS) as stall:

            async def chunks() -> AsyncIterator[bytes]:
                with regular_descriptor(source) as descriptor:
                    remaining, digest = manifest.byte_count, hashlib.sha256()
                    while remaining:
                        content = await asyncio.to_thread(
                            os.read, descriptor, min(TRANSFER_CHUNK_BYTES, remaining)
                        )
                        if not content:
                            raise ValueError("snapshot shorter than manifest")
                        remaining -= len(content)
                        digest.update(content)
                        stall.reschedule(loop.time() + FILE_TRANSFER_STALL_SECONDS)
                        yield content
                    if await asyncio.to_thread(os.read, descriptor, 1):
                        raise ValueError("snapshot exceeds manifest")
                    if digest.hexdigest() != manifest.sha256:
                        raise ValueError("snapshot digest differs from manifest")

            async with self._session("items").put(
                self.server_url + EXECUTOR_UPLOAD_PATH.format(sha256=manifest.sha256),
                data=chunks(),
                headers=self._headers
                | {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(manifest.byte_count),
                    EXECUTOR_FILE_UPLOAD_HEADER: described,
                },
                allow_redirects=False,
                compress=False,
            ) as response:
                if response.status != 204:
                    raise _error(response.status, await _read_bounded(response, MAX_ENVELOPE_BYTES))

    async def download_command_file(
        self, command_id: str, stored_file_id: str, target: Path
    ) -> str:
        """Stream route 6 into a new file at target; return the verified sha256."""
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            async with self._session("downloads").get(
                self.server_url
                + EXECUTOR_COMMAND_FILE_PATH.format(
                    command_id=command_id, stored_file_id=stored_file_id
                ),
                headers=self._headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(
                    total=None,
                    sock_connect=HTTP_CONNECT_TIMEOUT_SECONDS,
                    sock_read=FILE_TRANSFER_STALL_SECONDS,
                ),
            ) as response:
                if response.status != 200:
                    raise _error(response.status, await _read_bounded(response, MAX_ENVELOPE_BYTES))
                served = response.headers.get(EXECUTOR_FILE_SHA256_HEADER, "")
                if (
                    response.headers.get("Content-Encoding")
                    or not _SHA256.fullmatch(served)
                    or response.content_length is None
                ):
                    raise ValueError("executor download lacks its length or digest")
                count, digest = 0, hashlib.sha256()
                async for chunk in response.content.iter_chunked(TRANSFER_CHUNK_BYTES):
                    count += len(chunk)
                    if count > response.content_length:
                        raise ValueError("download exceeds its length")
                    digest.update(chunk)
                    await asyncio.to_thread(_write_all, descriptor, chunk)
                if count != response.content_length or digest.hexdigest() != served:
                    raise ValueError("download is short or corrupt")
                await asyncio.to_thread(os.fsync, descriptor)
                return served
        finally:
            os.close(descriptor)

    async def reset(self) -> None:
        sessions = tuple(self._sessions.values())
        self._sessions.clear()
        await self._close(sessions)

    async def close(self) -> None:
        await self.reset()

    @staticmethod
    async def _close(sessions: tuple[aiohttp.ClientSession, ...]) -> None:
        for session in sessions:
            try:
                await asyncio.wait_for(session.close(), HTTP_CLOSE_TIMEOUT_SECONDS)
            except (TimeoutError, OSError, aiohttp.ClientError):
                pass


def _write_all(descriptor: int, content: bytes) -> None:
    while content:
        count = os.write(descriptor, content)
        if count <= 0:
            raise OSError("short file write")
        content = content[count:]


def lease_refusal_code(refused: LeaseRefused) -> str:
    code = refused.error.get("code")
    return code if isinstance(code, str) else "unknown"
