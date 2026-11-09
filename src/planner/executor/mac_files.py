"""File transfers with the VPS: an item's uploads and a prompt's input downloads.

At most MAX_FILE_TRANSFERS run at once across both directions. An upload the VPS refuses
for good (403 or 400) is reported back on its item; anything else is retried with the
item. An input download retries until route 6 answers 403, 404 or 409, which means the
command is withdrawn, finished or fenced; the prompt is then refused.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from planner.conversation.message_content import MessageFile
from planner.core.task_settlement import run_on_worker_thread
from planner.executor.configuration import (
    INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS,
    INPUT_DOWNLOAD_RETRY_MAX_SECONDS,
    MAX_FILE_TRANSFERS,
    ExecutorPaths,
)
from planner.executor.files import contained_path, install_verified, safe_component
from planner.executor.mac_http import ExecutorHttpError, failure_name
from planner.executor.protocol import (
    ChildCommand,
    FileManifest,
    FileScope,
    FileUploadRequest,
    Steer,
    WritePrompt,
)
from planner.files.logic.paths import conversation_files_root

if TYPE_CHECKING:
    from planner.executor.contracts import MacHttpClient

_PERMANENT_UPLOAD_STATUSES: Final = (400, 403)
_FINAL_DOWNLOAD_STATUSES: Final = (403, 404, 409)


class UploadRefused(Exception):
    """The VPS refused an upload for good; the item carries this as its refusal."""


class InputDownloadRefused(Exception):
    """Route 6 will never serve this input: the command is withdrawn, finished or fenced."""


class MacFiles:
    def __init__(self, client: MacHttpClient, paths: ExecutorPaths) -> None:
        self.client, self.paths = client, paths
        self._transfers = asyncio.Semaphore(MAX_FILE_TRANSFERS)

    async def upload(self, upload: FileUploadRequest, source: Path) -> None:
        async with self._transfers:
            try:
                await self.client.upload_file(upload, source)
            except ExecutorHttpError as error:
                if error.status in _PERMANENT_UPLOAD_STATUSES:
                    raise UploadRefused(error.message or f"upload refused: {error.code}") from error
                raise

    async def download_inputs(self, command: ChildCommand) -> None:
        args = command.arguments
        if not isinstance(args, (WritePrompt, Steer)):
            return
        seen: set[str] = set()
        for content in (args.content, args.sender_content or ()):
            for piece in content:
                if isinstance(piece, MessageFile) and piece.stored_file_id not in seen:
                    seen.add(piece.stored_file_id)
                    await self._download_input(command, piece.stored_file_id)

    async def _download_input(self, command: ChildCommand, stored_file_id: str) -> None:
        conversation_id = command.child.conversation_id
        target = contained_path(
            conversation_files_root(self.paths.message_files_anchor),
            f"{safe_component(conversation_id)}/{safe_component(stored_file_id)}",
        )
        delay = INPUT_DOWNLOAD_RETRY_INITIAL_SECONDS
        failure: Exception
        while True:
            try:
                await self._download_once(
                    command.command_id, stored_file_id, target, conversation_id
                )
                return
            except ExecutorHttpError as error:
                if error.status in _FINAL_DOWNLOAD_STATUSES:
                    raise InputDownloadRefused(
                        f"input {stored_file_id} is no longer served ({error.status})"
                    ) from error
                failure = error
            except Exception as error:
                failure = error
            logging.getLogger(__name__).warning(
                "input download failed command=%s retry_in=%gs error=%s",
                command.command_id,
                delay,
                failure_name(failure),
            )
            await asyncio.sleep(delay)  # The loop clock is monotonic.
            delay = min(delay * 2, INPUT_DOWNLOAD_RETRY_MAX_SECONDS)

    async def _download_once(
        self, command_id: str, stored_file_id: str, target: Path, conversation_id: str
    ) -> None:
        self.paths.run.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.paths.run / (".input-" + uuid4().hex)
        try:
            async with self._transfers:
                digest = await self.client.download_command_file(
                    command_id, stored_file_id, temporary
                )
            manifest = FileManifest(
                FileScope.conversation,
                conversation_id,
                stored_file_id,
                digest,
                os.stat(temporary).st_size,
            )
            await run_on_worker_thread(install_verified, temporary, target, manifest)
        finally:
            temporary.unlink(missing_ok=True)
