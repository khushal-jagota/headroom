"""Keep agent output and immutable changed files until the VPS confirms them.

Each conversation sends its own items in ordered batches on route 4 and backs off by
itself, so one conversation's failures never hold up another's.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import sqlite3
import stat
import threading
import time
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from planner.execution_configuration import SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference
from planner.executor.configuration import (
    FILE_MODIFIED_WINDOW_NS,
    MAX_ITEM_BATCH_BYTES,
    MAX_ITEM_BATCH_ITEMS,
    MAX_LEASE_WAITING_CONVERSATIONS,
    OUTBOX_RETRY_INITIAL_SECONDS,
    OUTBOX_RETRY_MAX_SECONDS,
    ExecutorPaths,
)
from planner.executor.files import (
    contained_path,
    copy_hashed,
    directory_descriptor,
    fsync_directory,
    fsync_file,
    regular_descriptor,
    safe_component,
)
from planner.executor.mac_files import UploadRefused
from planner.executor.mac_http import failure_name
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileManifest,
    FileScope,
    FileSnapshot,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    ItemSubmission,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    WaitingConversation,
    encode_bounded,
    encode_value,
    payload_child,
    type_adapter,
)

if TYPE_CHECKING:
    from planner.executor.contracts import MacFilesApi, MacHttpClient


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    envelope: QueuedEnvelope
    receipt: AcceptanceReceipt | None = None

    @property
    def pending(self) -> bool:
        return self.receipt is None


@dataclass(frozen=True, slots=True)
class OutboxBacklog:
    """What the lease reports: items without a receipt, the age of the oldest of them
    in this process, and the waiting conversations, oldest first."""

    queue_depth: int
    oldest_item_age_ms: int | None
    waiting_conversations: tuple[WaitingConversation, ...]
    waiting_conversations_truncated: bool


class _RetryLater(Exception):
    """The VPS took its own time limit and asked for the batch again later."""


@dataclass
class _ConversationSending:
    """One conversation's send state between attempts."""

    refusals: dict[str, str] = field(default_factory=dict)
    uploaded: dict[str, set[FileManifest]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboxEntry:
    conversation_id: str
    kind: EnvelopeKind
    payload: JsonObject
    submission_id: str | None = None


DATABASE_NAME = "outbox.sqlite3"
SCHEMA = """
CREATE TABLE sender (singleton INTEGER PRIMARY KEY CHECK(singleton=1), sender_id TEXT NOT NULL);
CREATE TABLE items (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json BLOB NOT NULL,
    receipt_json BLOB
);
CREATE INDEX waiting ON items(conversation_id,sequence) WHERE receipt_json IS NULL;
CREATE TABLE item_files (
    sequence INTEGER NOT NULL REFERENCES items(sequence) ON DELETE CASCADE,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
    PRIMARY KEY(sequence,relative_path)
) WITHOUT ROWID;
CREATE INDEX item_files_blob ON item_files(sha256);
CREATE TABLE ticket_files (
    ticket_id TEXT NOT NULL, relative_path TEXT NOT NULL, byte_count INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL, sha256 TEXT NOT NULL,
    PRIMARY KEY(ticket_id,relative_path)
) WITHOUT ROWID;
"""


class MacOutbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.RLock()
        self._capture_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sending = asyncio.Lock()
        self._waiters: dict[str, set[asyncio.Future[AcceptanceReceipt]]] = {}
        self._closed = False
        self._capturing = 0
        # When this process first held each unreceipted item (monotonic seconds).
        self._first_held: dict[int, float] = {}
        self._database: sqlite3.Connection | None = None
        path = root / DATABASE_NAME
        # An incompatible format is preserved before creating even a lock file.
        if path.exists():
            with sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True) as check:
                if check.execute("PRAGMA user_version").fetchone()[0] != SPOOL_FORMAT_VERSION:
                    raise ValueError(
                        "unsupported outbox format; preserve the spool for compatible Panels"
                    )
        elif root.exists() and any(p.name != "lock" for p in root.iterdir()):
            raise ValueError("unsupported outbox format; preserve the spool for compatible Panels")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._process_lock = (root / "lock").open("a+b")
        try:
            fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            existed = path.exists()
            db = sqlite3.connect(path, check_same_thread=False)
            self._database = db
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA fullfsync=ON")
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA foreign_keys=ON")
            if not existed:
                db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                db.execute("INSERT INTO sender VALUES(1,?)", (uuid4().hex,))
                db.execute(f"PRAGMA user_version={SPOOL_FORMAT_VERSION}")
                db.commit()
                fsync_directory(root)
            self.sender_id: str = db.execute(
                "SELECT sender_id FROM sender WHERE singleton=1"
            ).fetchone()[0]
            self._blobs = root / "blobs"
            self._blobs.mkdir(exist_ok=True, mode=0o700)
            fsync_directory(root)
            with db:
                db.execute("DELETE FROM items WHERE receipt_json IS NOT NULL")
            opened = time.monotonic()
            self._first_held = {row[0]: opened for row in db.execute("SELECT sequence FROM items")}
            self._sweep_blobs()
        except BaseException:
            self.close()
            raise

    @property
    def _db(self) -> sqlite3.Connection:
        if self._closed or self._database is None:
            raise RuntimeError("outbox is closed")
        return self._database

    def _notify(self) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._wake.set)

    def _existing(self, submission_id: str | None) -> QueuedEnvelope | None:
        if submission_id is None:
            return None
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM items WHERE submission_id=?", (safe_component(submission_id),)
            ).fetchone()
            return None if row is None else self._envelope(row)

    def add(self, entry: OutboxEntry, *, ticket_id: str | None = None) -> QueuedEnvelope:
        """Add an item, or return the item already held under its submission ID, so
        publishing the same output again never fails and never adds it twice."""
        existing = self._existing(entry.submission_id)
        if existing is not None:
            return existing
        if ticket_id is None:
            return self._add(entry)
        with self._lock:
            capture_lock = self._capture_locks[ticket_id]
        with capture_lock:
            with self._lock:
                self._capturing += 1
            try:
                snapshots, updates = self._capture(ticket_id)
                return self._add(entry, snapshots=snapshots, updates=updates)
            finally:
                with self._lock:
                    self._capturing -= 1
                    self._sweep_blobs()

    def _capture(self, ticket_id: str) -> tuple[tuple[FileSnapshot, ...], list[tuple[object, ...]]]:
        root = ExecutorPaths(self.root.parent).artifacts / safe_component(ticket_id)
        snapshots: list[FileSnapshot] = []
        updates: list[tuple[object, ...]] = []
        checked_ns = time.time_ns()
        folders = [root] if root.exists() else []
        while folders:
            folder = folders.pop()
            try:
                with directory_descriptor(folder), os.scandir(folder) as entries:
                    ordered = sorted(entries, key=lambda item: item.name)
            except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
                logging.getLogger(__name__).warning(
                    "Skipping unreadable artifact directory: %s", folder
                )
                continue
            for entry in ordered:
                relative = Path(entry.path).relative_to(root).as_posix()
                try:
                    source = contained_path(root, relative)
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISDIR(info.st_mode) and not stat.S_ISREG(info.st_mode):
                        raise ValueError("artifact is not regular file")
                except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
                    logging.getLogger(__name__).warning(
                        "Skipping unreadable artifact: %s", relative
                    )
                    continue
                if stat.S_ISDIR(info.st_mode):
                    folders.append(source)
                elif captured := self._capture_file(ticket_id, relative, source, info, checked_ns):
                    snapshot, update = captured
                    snapshots.append(snapshot)
                    if update is not None:
                        updates.append(update)
        return tuple(snapshots), updates

    def _capture_file(
        self, ticket_id: str, relative: str, source: Path, info: os.stat_result, checked_ns: int
    ) -> tuple[FileSnapshot, tuple[object, ...] | None] | None:
        with self._lock:
            previous = self._db.execute(
                "SELECT byte_count,modified_ns,sha256 FROM ticket_files "
                "WHERE ticket_id=? AND relative_path=?",
                (ticket_id, relative),
            ).fetchone()
            if previous is not None and previous[:2] == (info.st_size, info.st_mtime_ns):
                # A refusal invalidates the index; it must not slip between these reads.
                waiting = self._db.execute(
                    "SELECT 1 FROM item_files JOIN items USING(sequence) "
                    "WHERE sha256=? AND receipt_json IS NULL LIMIT 1",
                    (previous["sha256"],),
                ).fetchone()
                if waiting:
                    manifest = FileManifest(
                        FileScope.ticket, ticket_id, relative, previous["sha256"], info.st_size
                    )
                    return FileSnapshot(manifest, self._blobs / manifest.sha256), None
                return None
        with ExitStack() as sources:
            try:
                incoming = sources.enter_context(regular_descriptor(source))
            except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
                logging.getLogger(__name__).warning("Skipping unreadable artifact: %s", relative)
                return None
            temporary = self._blobs / (".tmp-" + uuid4().hex)
            try:
                with os.fdopen(
                    os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
                ) as outgoing:
                    before = os.fstat(incoming)
                    sha256, count = copy_hashed(
                        incoming, outgoing.fileno(), skip_unreadable_source=True
                    )
                    after = os.fstat(incoming)
                    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_size,
                        after.st_mtime_ns,
                        after.st_ctime_ns,
                    ) or count != after.st_size:
                        logging.getLogger(__name__).warning(
                            "Skipping unreadable or changing artifact: %s", relative
                        )
                        return None
                    outgoing.flush()
                    fsync_file(outgoing.fileno())
                target = self._blobs / sha256
                if not target.exists():
                    os.replace(temporary, target)
                fsync_directory(self._blobs)
                manifest = FileManifest(FileScope.ticket, ticket_id, relative, sha256, count)
                # A recent timestamp is deliberately not reusable on the next check.
                modified_ns = (
                    -1
                    if checked_ns - after.st_mtime_ns <= FILE_MODIFIED_WINDOW_NS
                    else after.st_mtime_ns
                )
                return FileSnapshot(manifest, target), (
                    ticket_id,
                    relative,
                    count,
                    modified_ns,
                    sha256,
                )
            finally:
                temporary.unlink(missing_ok=True)

    def _add(
        self,
        entry: OutboxEntry,
        *,
        snapshots: tuple[FileSnapshot, ...] = (),
        updates: list[tuple[object, ...]] | tuple[()] = (),
    ) -> QueuedEnvelope:
        with self._lock:
            safe_component(entry.conversation_id)
            submission = safe_component(entry.submission_id or uuid4().hex)
            payload = {
                "payload": entry.payload,
                "files": encode_value(tuple(s.manifest for s in snapshots)),
            }
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            child = payload_child(entry.payload)
            child_id = str(child.get("child_id", "")) if child is not None else ""
            existing = self._existing(entry.submission_id)
            if existing is not None:
                return existing
            with self._db:
                cursor = self._db.execute(
                    "INSERT INTO items"
                    "(submission_id,conversation_id,child_id,kind,payload_json) "
                    "VALUES(?,?,?,?,?)",
                    (submission, entry.conversation_id, child_id, entry.kind.value, encoded),
                )
                assert cursor.lastrowid is not None
                self._db.executemany(
                    "INSERT INTO item_files VALUES(?,?,?,?)",
                    [
                        (
                            cursor.lastrowid,
                            m.manifest.relative_path,
                            m.manifest.sha256,
                            m.manifest.byte_count,
                        )
                        for m in snapshots
                    ],
                )
                self._db.executemany(
                    "INSERT INTO ticket_files VALUES(?,?,?,?,?) "
                    "ON CONFLICT(ticket_id,relative_path) DO UPDATE SET "
                    "byte_count=excluded.byte_count,modified_ns=excluded.modified_ns,"
                    "sha256=excluded.sha256",
                    updates,
                )
                envelope = self._envelope(
                    self._db.execute(
                        "SELECT * FROM items WHERE sequence=?", (cursor.lastrowid,)
                    ).fetchone()
                )
                # Reject an unsendable item before acknowledging its durable addition.
                encode_bounded(ItemSubmission(envelope))
            self._first_held[envelope.sequence] = time.monotonic()
            self._notify()  # Only a committed item may wake sending.
            return envelope

    def _envelope(self, row: sqlite3.Row) -> QueuedEnvelope:
        value = json.loads(row["payload_json"])
        files = type_adapter(tuple[FileManifest, ...]).validate_python(value["files"])
        payload: JsonObject = type_adapter(JsonObject).validate_python(value["payload"])
        kind = EnvelopeKind(row["kind"])
        return QueuedEnvelope(
            self.sender_id,
            row["conversation_id"],
            row["sequence"],
            row["submission_id"],
            kind,
            payload,
            files,
        )

    def lookup(self, submission_id: str) -> OutboxRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM items WHERE submission_id=?", (submission_id,)
            ).fetchone()
            if row is None:
                return None
            receipt = (
                type_adapter(AcceptanceReceipt).validate_json(row["receipt_json"])
                if row["receipt_json"] is not None
                else None
            )
            return OutboxRecord(self._envelope(row), receipt)

    async def wait_confirmed(self, submission_id: str, timeout: float) -> AcceptanceReceipt | None:
        self._loop = asyncio.get_running_loop()
        future: asyncio.Future[AcceptanceReceipt] = self._loop.create_future()
        self._waiters.setdefault(submission_id, set()).add(future)
        try:
            row = await asyncio.to_thread(self.lookup, submission_id)
            if row is not None and row.receipt is not None:
                return row.receipt
            try:
                return await asyncio.wait_for(asyncio.shield(future), max(0, timeout))
            except TimeoutError:
                return None
        finally:
            self._waiters[submission_id].discard(future)
            if not self._waiters[submission_id]:
                del self._waiters[submission_id]
            future.cancel()

    def pending(self) -> tuple[QueuedEnvelope, ...]:
        with self._lock:
            return tuple(
                self._envelope(row)
                for row in self._db.execute(
                    "SELECT * FROM items WHERE receipt_json IS NULL ORDER BY sequence"
                )
            )

    def pending_conversations(self) -> tuple[str, ...]:
        """Conversations with a waiting item, the one waiting longest first."""
        with self._lock:
            return tuple(
                row[0]
                for row in self._db.execute(
                    "SELECT conversation_id FROM items WHERE receipt_json IS NULL "
                    "GROUP BY conversation_id ORDER BY MIN(sequence)"
                )
            )

    def pending_head(self, conversation_id: str) -> QueuedEnvelope | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM items WHERE receipt_json IS NULL AND conversation_id=? "
                "ORDER BY sequence LIMIT 1",
                (conversation_id,),
            ).fetchone()
            return None if row is None else self._envelope(row)

    def lease_backlog(self) -> OutboxBacklog:
        with self._lock:
            count = int(
                self._db.execute(
                    "SELECT COUNT(*) FROM items WHERE receipt_json IS NULL"
                ).fetchone()[0]
            )
            rows = self._db.execute(
                "SELECT conversation_id,MIN(sequence) FROM items WHERE receipt_json IS NULL "
                "GROUP BY conversation_id ORDER BY MIN(sequence) LIMIT ?",
                (MAX_LEASE_WAITING_CONVERSATIONS + 1,),
            ).fetchall()
            held = self._first_held.get(rows[0][1]) if rows else None
        return OutboxBacklog(
            count,
            None if held is None else int((time.monotonic() - held) * 1000),
            tuple(
                WaitingConversation(conversation, sequence)
                for conversation, sequence in rows[:MAX_LEASE_WAITING_CONVERSATIONS]
            ),
            len(rows) > MAX_LEASE_WAITING_CONVERSATIONS,
        )

    def pending_count(self) -> int:
        with self._lock:
            return int(
                self._db.execute(
                    "SELECT COUNT(*) FROM items WHERE receipt_json IS NULL"
                ).fetchone()[0]
            )

    def snapshots(self, envelope: QueuedEnvelope) -> tuple[FileSnapshot, ...]:
        return tuple(FileSnapshot(m, self._blobs / m.sha256) for m in envelope.file_dependencies)

    def acknowledge(self, receipt: AcceptanceReceipt) -> None:
        with self._lock:
            row = self.lookup(receipt.submission_id)
            if row is None:
                return
            envelope = row.envelope
            if receipt.sender_id != self.sender_id:
                raise ValueError("receipt identity mismatch")
            with self._db:
                if receipt.disposition is not ReceiptDisposition.accepted:
                    for manifest in envelope.file_dependencies:
                        self._db.execute(
                            "DELETE FROM ticket_files WHERE ticket_id=? AND relative_path=? "
                            "AND sha256=?",
                            (manifest.owner_id, manifest.relative_path, manifest.sha256),
                        )
                if envelope.kind is EnvelopeKind.cli_write:
                    self._db.execute(
                        "UPDATE items SET receipt_json=? WHERE submission_id=?",
                        (type_adapter(AcceptanceReceipt).dump_json(receipt), receipt.submission_id),
                    )
                else:
                    self._db.execute(
                        "DELETE FROM items WHERE submission_id=?", (receipt.submission_id,)
                    )
                if envelope.kind is EnvelopeKind.child_stopped:
                    child = envelope.payload.get("child")
                    assert isinstance(child, dict)
                    self._db.execute(
                        "DELETE FROM items WHERE child_id=? AND receipt_json IS NOT NULL",
                        (child["child_id"],),
                    )
            self._first_held.pop(envelope.sequence, None)
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._confirmed, receipt)
            self._sweep_blobs()

    def _confirmed(self, receipt: AcceptanceReceipt) -> None:
        for waiter in self._waiters.get(receipt.submission_id, ()):
            if not waiter.done():
                waiter.set_result(receipt)

    def _sweep_blobs(self) -> None:
        if self._capturing or not any(self._blobs.iterdir()):
            return
        referenced = {
            row[0]
            for row in self._db.execute(
                "SELECT DISTINCT sha256 FROM item_files JOIN items USING(sequence) "
                "WHERE receipt_json IS NULL"
            )
        }
        for path in self._blobs.iterdir():
            if path.name not in referenced:
                path.unlink()
        fsync_directory(self._blobs)

    def _batch(self, conversation_id: str, state: _ConversationSending) -> ItemBatch | None:
        """The conversation's next items within the batch budget, always at least one.
        A batch ends right after its first item that is not a plain backend event, so a
        resend after a lost reply gets that item's real stored receipt."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM items WHERE receipt_json IS NULL AND conversation_id=? "
                "ORDER BY sequence LIMIT ?",
                (conversation_id, MAX_ITEM_BATCH_ITEMS),
            ).fetchall()
            envelopes = [self._envelope(row) for row in rows]
        items: list[ItemSubmission] = []
        size = len(type_adapter(ItemBatch).dump_json(ItemBatch(self.sender_id, ())))
        for envelope in envelopes:
            item = ItemSubmission(envelope, state.refusals.get(envelope.submission_id))
            item_size = len(type_adapter(ItemSubmission).dump_json(item)) + 1
            if items and size + item_size > MAX_ITEM_BATCH_BYTES:
                break
            items.append(item)
            size += item_size
            if envelope.kind is not EnvelopeKind.backend_event or envelope.file_dependencies:
                break
        return ItemBatch(self.sender_id, tuple(items)) if items else None

    async def _apply_reply(
        self,
        files: MacFilesApi,
        batch: ItemBatch,
        reply: ItemBatchReply,
        state: _ConversationSending,
    ) -> None:
        """Acknowledge the receipted prefix, then act on where the VPS stopped."""
        if len(reply.receipts) > len(batch.items):
            raise ValueError("VPS answered more items than the batch holds")
        for item, receipt in zip(batch.items, reply.receipts, strict=False):
            if receipt.submission_id != item.envelope.submission_id:
                raise ValueError("receipt names another item")
            await asyncio.to_thread(self.acknowledge, receipt)
            state.refusals.pop(item.envelope.submission_id, None)
            state.uploaded.pop(item.envelope.submission_id, None)
        stop = reply.stop
        if stop is None:
            if len(reply.receipts) != len(batch.items):
                raise ValueError("VPS answered part of a batch without saying why")
            return
        if len(reply.receipts) == len(batch.items) or (
            batch.items[len(reply.receipts)].envelope.sequence != stop.sequence
        ):
            raise ValueError("VPS stopped at an item outside this batch")
        if stop.reason == "retry_later":
            raise _RetryLater(stop.message)
        envelope = batch.items[len(reply.receipts)].envelope
        uploaded = state.uploaded.setdefault(envelope.submission_id, set())
        # A reply that makes no progress fails the attempt, so it waits its back-off
        # instead of resending at once without end.
        if not set(stop.missing_files) - uploaded:
            # The VPS lost what it was sent (a restart between upload and resend): the
            # next attempt, after its back-off, uploads again.
            state.uploaded.pop(envelope.submission_id, None)
            raise ValueError("VPS asked again for files already uploaded")
        snapshots = {snapshot.manifest: snapshot for snapshot in self.snapshots(envelope)}
        child = payload_child(envelope.payload)
        if child is None:
            raise ValueError("file upload lacks assigned child")
        reference = type_adapter(ChildReference).validate_python(child)
        for manifest in stop.missing_files:
            if manifest not in snapshots:
                raise ValueError("VPS requested a file outside this item")
            try:
                await files.upload(
                    FileUploadRequest(manifest, reference), snapshots[manifest].local_path
                )
            except UploadRefused as error:
                # This item cannot use the file. The resend carries the refusal, which
                # the VPS records as the item's refused receipt.
                state.refusals[envelope.submission_id] = str(error)
                return
            uploaded.add(manifest)

    async def _send_conversation(
        self, client: MacHttpClient, files: MacFilesApi, conversation_id: str
    ) -> None:
        """Send one conversation's items in order, one batch in flight, until none wait.

        A failure of this conversation's batch, including reading it from the outbox,
        backs off and retries it alone.
        """
        delay = OUTBOX_RETRY_INITIAL_SECONDS
        attempt = 0
        state = _ConversationSending()
        while not self._closed:
            batch: ItemBatch | None = None
            try:
                batch = await asyncio.to_thread(self._batch, conversation_id, state)
                if batch is None:
                    return
                reply = await client.post_items(conversation_id, batch)
                await self._apply_reply(files, batch, reply, state)
            except Exception as error:
                if self._closed:
                    return  # Sending is ending; the item stays queued.
                attempt += 1
                logging.getLogger(__name__).warning(
                    "outbox send failed conversation=%s sequence=%s attempt=%s"
                    " retry_in=%gs error=%s",
                    conversation_id,
                    None if batch is None else batch.items[0].envelope.sequence,
                    attempt,
                    delay,
                    "retry_later" if isinstance(error, _RetryLater) else failure_name(error),
                )
                await asyncio.sleep(delay)
                if isinstance(error, _RetryLater):
                    # The VPS took its own time limit to answer, so the resend need not
                    # wait longer each time.
                    delay = OUTBOX_RETRY_INITIAL_SECONDS
                else:
                    delay = min(delay * 2, OUTBOX_RETRY_MAX_SECONDS)
            else:
                delay, attempt = OUTBOX_RETRY_INITIAL_SECONDS, 0

    async def send_pending(self, client: MacHttpClient, files: MacFilesApi) -> None:
        """Send until the outbox closes, each conversation in its own task."""
        self._loop = asyncio.get_running_loop()
        async with self._sending:
            senders: dict[str, asyncio.Task[None]] = {}
            wake: asyncio.Task[bool] | None = None
            try:
                while not self._closed:
                    self._wake.clear()
                    try:
                        waiting = await asyncio.to_thread(self.pending_conversations)
                    except (OSError, sqlite3.Error) as error:
                        logging.getLogger(__name__).warning(
                            "outbox scan failed error=%s", failure_name(error)
                        )
                        await asyncio.sleep(OUTBOX_RETRY_INITIAL_SECONDS)
                        continue
                    for conversation_id in waiting:
                        if conversation_id not in senders:
                            senders[conversation_id] = asyncio.create_task(
                                self._send_conversation(client, files, conversation_id)
                            )
                    wake = asyncio.create_task(self._wake.wait())
                    await asyncio.wait(
                        (*senders.values(), wake), return_when=asyncio.FIRST_COMPLETED
                    )
                    wake.cancel()
                    await asyncio.gather(wake, return_exceptions=True)
                    for conversation_id, sender in tuple(senders.items()):
                        if sender.done():
                            del senders[conversation_id]
                            if not sender.cancelled() and sender.exception() is not None:
                                logging.getLogger(__name__).warning(
                                    "outbox sender ended conversation=%s error=%s",
                                    conversation_id,
                                    failure_name(sender.exception() or Exception()),
                                )
            finally:
                if wake is not None:
                    wake.cancel()
                for sender in senders.values():
                    sender.cancel()
                await asyncio.gather(
                    *senders.values(), *([wake] if wake else []), return_exceptions=True
                )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._database is not None:
                self._database.close()
            self._process_lock.close()
        self._notify()
