"""File transfers as their own requests: uploads into staging, and input downloads.

An upload (route 5) streams one file's bytes into private staging beside the
database. It is verified against its manifest and installed into the served tree only
by the item that names it, under that item's receipt transaction. A transfer whose
bytes stop moving for FILE_TRANSFER_STALL_SECONDS fails, and its temporary file is
discarded. An input download (route 6) serves one file of a prompt or steer that is
still waiting or delivered, and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from planner.conversation.message_content import MessageFile
from planner.conversation.message_files import ConversationMessageFiles, MessageFileMissing
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.execution_identity import ExecutorIdentity
from planner.executor.configuration import FILE_CHUNK_BYTES, FILE_TRANSFER_STALL_SECONDS
from planner.executor.files import directory_descriptor, file_digest
from planner.executor.protocol import FileUploadRequest, Steer, WritePrompt
from planner.executor.vps_inbox import MacInbox
from planner.executor.vps_mailbox import SqliteCommandMailbox


@dataclass(frozen=True, slots=True)
class InputFile:
    """A file a waiting command carries, as route 6 serves it."""

    path: Path
    sha256: str
    byte_count: int


def _write_all(descriptor: int, content: bytes) -> None:
    while content:
        count = os.write(descriptor, content)
        if count <= 0:
            raise OSError("short file write")
        content = content[count:]


class VpsFiles:
    def __init__(
        self,
        db_path: str,
        inbox: MacInbox,
        mailbox: SqliteCommandMailbox,
        message_files: ConversationMessageFiles,
    ) -> None:
        self.db_path = db_path
        self.inbox = inbox
        self.mailbox = mailbox
        self.message_files = message_files
        self.staging = Path(db_path).resolve().parent / "executor-uploads"

    async def upload(
        self,
        identity: ExecutorIdentity,
        sha256: str,
        upload: FileUploadRequest,
        content_length: int | None,
        chunks: AsyncIterator[bytes],
    ) -> None:
        """Route 5. Bytes are in staging when this returns; the caller keeps ownership of
        the install until it ends, so a dropped request never leaves it half done."""
        del identity  # Fenced by the route; authority is the child's assignment.
        manifest = upload.manifest
        if manifest.sha256 != sha256 or content_length != manifest.byte_count:
            raise PlannerError(ErrorCode.validation, "upload does not match its manifest")
        try:
            await run_on_worker_thread(self.inbox.authorize_upload, upload)
        except ValueError as error:
            raise PlannerError(ErrorCode.validation, str(error)) from error
        await run_on_worker_thread(self.staging.mkdir, parents=True, exist_ok=True)
        incoming = self.staging / f".incoming-{uuid4().hex}"
        descriptor = await run_on_worker_thread(
            os.open, incoming, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            try:
                count, digest = 0, hashlib.sha256()
                iterator = aiter(chunks)
                while True:
                    try:
                        chunk = await asyncio.wait_for(anext(iterator), FILE_TRANSFER_STALL_SECONDS)
                    except StopAsyncIteration:
                        break
                    except TimeoutError as error:
                        raise PlannerError(
                            ErrorCode.retry_later, "the upload stopped moving; send it again"
                        ) from error
                    if count + len(chunk) > manifest.byte_count:
                        raise PlannerError(ErrorCode.validation, "upload exceeds its manifest")
                    for start in range(0, len(chunk), FILE_CHUNK_BYTES):
                        piece = chunk[start : start + FILE_CHUNK_BYTES]
                        await run_on_worker_thread(_write_all, descriptor, piece)
                        digest.update(piece)
                        count += len(piece)
                if count != manifest.byte_count or digest.hexdigest() != manifest.sha256:
                    raise PlannerError(ErrorCode.validation, "upload is short or corrupt")
                await run_on_worker_thread(os.fsync, descriptor)
            finally:
                os.close(descriptor)
            try:
                await run_on_worker_thread(self.inbox.store_upload, upload, incoming)
            except ValueError as error:
                raise PlannerError(ErrorCode.validation, str(error)) from error
        finally:
            await run_on_worker_thread(self._discard, incoming)

    def _discard(self, incoming: Path) -> None:
        with directory_descriptor(incoming.parent) as parent:
            try:
                os.unlink(incoming.name, dir_fd=parent)
            except FileNotFoundError:
                pass

    async def input_file(
        self, identity: ExecutorIdentity, command_id: str, stored_file_id: str
    ) -> InputFile:
        """Route 6's scope: a file in the content of a command still waiting or
        delivered, for the current process. Not found otherwise."""
        command = await run_on_worker_thread(self.mailbox.command_for_input, command_id)
        missing = PlannerError(ErrorCode.not_found, "no such input for this command")
        if command is None or command.child.executor_incarnation != identity.executor_incarnation:
            raise missing
        args = command.arguments
        allowed = (
            {
                piece.stored_file_id
                for content in (args.content, args.sender_content or ())
                for piece in content
                if isinstance(piece, MessageFile)
            }
            if isinstance(args, (WritePrompt, Steer))
            else set()
        )
        if stored_file_id not in allowed:
            raise missing
        await run_on_worker_thread(self.inbox.runs.authority, command.child)
        try:
            path = await run_on_worker_thread(
                self.message_files.path_of, command.child.conversation_id, stored_file_id
            )
            sha256, byte_count = await run_on_worker_thread(file_digest, path)
        except (MessageFileMissing, FileNotFoundError) as error:
            raise missing from error
        return InputFile(path, sha256, byte_count)
