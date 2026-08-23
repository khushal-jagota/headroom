"""The files a conversation's messages carry, kept beside the record.

A picture, document, data file, or sound is bytes, and bytes do not go in the row. A
record is read whole every time a conversation is opened and replayed on every tail. Raw
file bytes inside a row would be shipped on every read. The row names the file, and the
file lives here.

Keeping them as files rather than as blobs is what lets one vocabulary run the whole way.
A backend is handed the same value the record holds. Adapters choose the backend's native
image or file route, and the browser links to the managed bytes. A blob in the row would
require a second message vocabulary. The managed id keeps one durable message shape.

**How long they last: as long as the record does, which is forever.** Nothing in Panels
deletes a conversation. Resetting one kills its activity and unlinks it, and says so in
its own words; the row and its rows stay. Deleting a Ticket removes the Ticket and leaves
its conversation behind, unpointed-at. So a file removed on either of those would turn a
message the record still holds into a broken picture — the record would claim something it
could no longer show. The files have the lifetime of the rows that name them. That is the
same bargain managed ticket files already make under the same root, and the day anybody
wants a reaper it is one reaper for all three.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from planner.files.logic.paths import conversation_files_root, resolve_conversation_file

# What a kept file is called. The name is minted here rather than taken from whatever the
# sender called the file: a name that arrived from outside is untrusted text, and the name
# it arrived under is kept in the message's own piece where it is read rather than walked.
STORED_FILE_ID_PREFIX = "f_"

# What is written beside a kept file to say what it is. A file that says what it is can be
# handed back without reading the conversation it belongs to — the alternative was looking
# the type up in the record, which meant reading a whole notebook to serve one picture.
MEDIA_TYPE_SUFFIX = ".media-type"


class MessageFileMissing(FileNotFoundError):
    """A message names a kept file that is not there.

    The record still says what was sent — a picture, its media type, what it was called —
    so the message reads. Only the bytes are gone.
    """


@dataclass(frozen=True, slots=True)
class KeptMessageFile:
    """One file kept for a conversation, as it was written."""

    conversation_id: str
    stored_file_id: str
    media_type: str
    absolute_path: Path
    byte_count: int


def new_stored_file_id() -> str:
    """Mint the name a kept file is known by. Safe by construction, never by inspection."""
    return f"{STORED_FILE_ID_PREFIX}{uuid4().hex}"


class ConversationMessageFiles:
    """Where one server keeps the files its conversations' messages carry."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = db_path

    async def keep(
        self, conversation_id: str, contents: bytes, *, media_type: str
    ) -> KeptMessageFile:
        """Write bytes for this conversation and return the file they were kept as.

        The bytes are on disk before the message that names them is sent, so a message the
        record holds never names a file that was not written. A message that is then
        refused leaves the file behind unnamed, which costs a few bytes and loses nothing.

        ``media_type`` is written beside the bytes so the file can say what it is when it
        is handed back. It is the type the sender or the backend stated, kept as it was
        given: what a thing is, is not something to guess from its bytes at serving time.
        """
        return await asyncio.to_thread(
            self._keep_sync, conversation_id, contents, media_type
        )

    async def read(self, conversation_id: str, stored_file_id: str) -> bytes:
        """The bytes of one kept file. Raises ``MessageFileMissing`` when they are gone."""
        return await asyncio.to_thread(self._read_sync, conversation_id, stored_file_id)

    async def media_type_of(self, conversation_id: str, stored_file_id: str) -> str | None:
        """What a kept file says it is, or nothing when it never said.

        Absent rather than guessed: a file written before it said, or one whose note has
        gone, is served as bytes of no stated kind rather than as something sniffed.
        """
        return await asyncio.to_thread(
            self._media_type_sync, conversation_id, stored_file_id
        )

    def path_of(self, conversation_id: str, stored_file_id: str) -> Path:
        """Where a kept file is, for the backends that would rather be given a path.

        Raises ``MessageFileMissing`` when there is nothing there, because a path handed to
        a backend has to be a path to something.
        """
        try:
            return resolve_conversation_file(self._db_path, conversation_id, stored_file_id)
        except ValueError as not_there:
            raise MessageFileMissing(stored_file_id) from not_there

    # --- inside the worker thread ---

    def _keep_sync(
        self, conversation_id: str, contents: bytes, media_type: str
    ) -> KeptMessageFile:
        folder = conversation_files_root(self._db_path) / conversation_id
        folder.mkdir(parents=True, exist_ok=True)
        stored_file_id = new_stored_file_id()
        target = folder / stored_file_id
        target.write_bytes(contents)
        # After the bytes: a note beside a file that is not there says nothing useful.
        (folder / f"{stored_file_id}{MEDIA_TYPE_SUFFIX}").write_text(
            media_type, encoding="utf-8"
        )
        return KeptMessageFile(
            conversation_id=conversation_id,
            stored_file_id=stored_file_id,
            media_type=media_type,
            absolute_path=target,
            byte_count=len(contents),
        )

    def _read_sync(self, conversation_id: str, stored_file_id: str) -> bytes:
        return self.path_of(conversation_id, stored_file_id).read_bytes()

    def _media_type_sync(self, conversation_id: str, stored_file_id: str) -> str | None:
        beside = self.path_of(conversation_id, stored_file_id).parent / (
            f"{stored_file_id}{MEDIA_TYPE_SUFFIX}"
        )
        try:
            stated = beside.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return stated or None
