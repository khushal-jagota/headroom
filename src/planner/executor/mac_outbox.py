"""Keep agent output and immutable changed files until the VPS confirms them."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import sqlite3
import stat
import threading
import time
from collections import defaultdict
from contextlib import ExitStack
from pathlib import Path
from typing import cast
from uuid import uuid4

from planner.execution_configuration import SPOOL_FORMAT_VERSION
from planner.executor.configuration import (
    FILE_CHUNK_BYTES,
    FILE_MODIFIED_WINDOW_NS,
    MAX_TRANSFERS,
    OUTBOX_BUSY_TIMEOUT_MS,
    OUTBOX_RETRY_SECONDS,
    ExecutorPaths,
)
from planner.executor.files import (
    contained_path,
    directory_descriptor,
    fsync_directory,
    fsync_file,
    regular_descriptor,
    safe_component,
)
from planner.executor.link import Link
from planner.executor.link_files import put_file
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    EnvelopeRefusal,
    FileManifest,
    FileScope,
    FileSnapshot,
    FileTransfer,
    JsonObject,
    OutboxEntry,
    OutboxLink,
    OutboxRecord,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
    as_object,
    encode_message,
    encode_value,
    envelope_payload_digest,
    type_adapter,
)

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
        self.transfers = 0
        self.root = root
        self._lock = threading.RLock()
        self._capture_locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._wake = asyncio.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sending = asyncio.Lock()
        self._waiters: dict[str, set[asyncio.Future[AcceptanceReceipt]]] = {}
        self._closed = False
        self._capturing = 0
        self._database: sqlite3.Connection | None = None
        path = root / DATABASE_NAME
        # An old draft format is refused before creating even a lock file.
        if path.exists():
            with sqlite3.connect(f"{path.absolute().as_uri()}?mode=ro", uri=True) as check:
                if check.execute("PRAGMA user_version").fetchone()[0] != SPOOL_FORMAT_VERSION:
                    raise ValueError("unsupported outbox format; rebuild the unshipped spool")
        elif root.exists() and any(p.name != "lock" for p in root.iterdir()):
            raise ValueError("unsupported outbox format; rebuild the unshipped spool")
        missing: list[Path] = []
        ancestor = root
        while not ancestor.exists():
            missing.append(ancestor)
            ancestor = ancestor.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o700)
            fsync_directory(directory.parent)
        self._process_lock = (root / "lock").open("a+b")
        try:
            fcntl.flock(self._process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            existed = path.exists()
            db = sqlite3.connect(
                path, timeout=OUTBOX_BUSY_TIMEOUT_MS / 1000, check_same_thread=False
            )
            self._database = db
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

    def add(self, entry: OutboxEntry, *, ticket_id: str | None = None) -> QueuedEnvelope:
        if ticket_id is None:
            return self._add(entry)
        with self._lock:
            capture_lock = self._capture_locks[ticket_id]
        with capture_lock:
            if entry.submission_id and self.lookup(entry.submission_id) is not None:
                return self._add(entry)
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

        def walk(folder: Path) -> None:
            try:
                with directory_descriptor(folder), os.scandir(folder) as entries:
                    ordered = sorted(entries, key=lambda item: item.name)
            except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
                logging.getLogger(__name__).warning(
                    "Skipping unreadable artifact directory: %s", folder
                )
                return
            for entry in ordered:
                with ExitStack() as sources:
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
                        walk(source)
                        continue
                    with self._lock:
                        previous = self._db.execute(
                            "SELECT byte_count,modified_ns,sha256 FROM ticket_files "
                            "WHERE ticket_id=? AND relative_path=?",
                            (ticket_id, relative),
                        ).fetchone()
                        if previous is not None and previous[:2] == (
                            info.st_size,
                            info.st_mtime_ns,
                        ):
                            # Read both facts under one lock: a refusal invalidates the
                            # index and must not slip between these two reads.
                            waiting = self._db.execute(
                                "SELECT 1 FROM item_files JOIN items USING(sequence) "
                                "WHERE sha256=? AND receipt_json IS NULL LIMIT 1",
                                (previous[2],),
                            ).fetchone()
                            if waiting:
                                manifest = FileManifest(
                                    FileScope.ticket, ticket_id, relative, previous[2], info.st_size
                                )
                                snapshots.append(FileSnapshot(manifest, self._blobs / previous[2]))
                            continue
                    try:
                        incoming = sources.enter_context(regular_descriptor(source))
                    except (FileNotFoundError, NotADirectoryError, PermissionError, ValueError):
                        logging.getLogger(__name__).warning(
                            "Skipping unreadable artifact: %s", relative
                        )
                        continue
                    temporary = self._blobs / (".tmp-" + uuid4().hex)
                    digest = hashlib.sha256()
                    count = 0
                    try:
                        with os.fdopen(
                            os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                            "wb",
                        ) as outgoing:
                            before = os.fstat(incoming)
                            while True:
                                try:
                                    block = os.read(incoming, FILE_CHUNK_BYTES)
                                except (FileNotFoundError, NotADirectoryError, PermissionError):
                                    count = -1
                                    break
                                if not block:
                                    break
                                outgoing.write(block)
                                digest.update(block)
                                count += len(block)
                            after = os.fstat(incoming)
                            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                                after.st_size,
                                after.st_mtime_ns,
                                after.st_ctime_ns,
                            ) or count != after.st_size:
                                logging.getLogger(__name__).warning(
                                    "Skipping unreadable or changing artifact: %s", relative
                                )
                                continue
                            outgoing.flush()
                            fsync_file(outgoing.fileno())
                        sha256 = digest.hexdigest()
                        target = self._blobs / sha256
                        if not target.exists():
                            os.replace(temporary, target)
                        fsync_directory(self._blobs)
                        manifest = FileManifest(
                            FileScope.ticket, ticket_id, relative, sha256, count
                        )
                        # A recent timestamp is deliberately not reusable on the next check.
                        modified_ns = (
                            -1
                            if checked_ns - after.st_mtime_ns <= FILE_MODIFIED_WINDOW_NS
                            else after.st_mtime_ns
                        )
                        updates.append((ticket_id, relative, count, modified_ns, sha256))
                        snapshots.append(FileSnapshot(manifest, target))
                    finally:
                        temporary.unlink(missing_ok=True)

        if root.exists():
            walk(root)
        return tuple(snapshots), updates

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
            previous = self.lookup(submission)
            if previous is not None:
                old = previous.envelope
                if (old.conversation_id, old.kind, old.payload) != (
                    entry.conversation_id,
                    entry.kind,
                    entry.payload,
                ) or (
                    snapshots
                    and old.file_dependencies != tuple(s.manifest for s in snapshots)
                ):
                    raise ValueError("submission id reused with different content")
                return old
            payload = {
                "payload": entry.payload,
                "files": encode_value(tuple(s.manifest for s in snapshots)),
            }
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            child = entry.payload.get("child")
            if not isinstance(child, dict):
                ask = entry.payload.get("ask")
                child = ask.get("child") if isinstance(ask, dict) else None
            child_id = str(child.get("child_id", "")) if isinstance(child, dict) else ""
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
                    (
                        cursor.lastrowid,
                        submission,
                        entry.conversation_id,
                        child_id,
                        entry.kind.value,
                        encoded,
                        None,
                    )
                )
                # Reject an unsendable item before acknowledging its durable addition.
                encode_message(
                    WireMessage(WireMessageKind.envelope, "0" * 32, self._object(envelope))
                )
            self._notify()  # Only a committed item may wake sending.
            return envelope

    @staticmethod
    def _object(value: object) -> JsonObject:
        return cast(JsonObject, type_adapter(JsonObject).validate_python(encode_value(value)))

    def _envelope(self, row: tuple[object, ...]) -> QueuedEnvelope:
        sequence, submission, conversation, _, kind, encoded, _ = row
        assert (
            isinstance(sequence, int)
            and isinstance(submission, str)
            and isinstance(conversation, str)
        )
        assert isinstance(kind, str) and isinstance(encoded, (bytes, str))
        value = json.loads(encoded)
        files = type_adapter(tuple[FileManifest, ...]).validate_python(value["files"])
        payload: JsonObject = type_adapter(JsonObject).validate_python(value["payload"])
        digest = envelope_payload_digest(
            payload,
            files,
            sender_id=self.sender_id,
            conversation_id=conversation,
            sequence=sequence,
            submission_id=submission,
            kind=EnvelopeKind(kind),
        )
        return QueuedEnvelope(
            self.sender_id,
            conversation,
            sequence,
            submission,
            EnvelopeKind(kind),
            payload,
            digest,
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
                type_adapter(AcceptanceReceipt).validate_json(row[6])
                if row[6] is not None
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

    def pending_heads(self) -> tuple[QueuedEnvelope, ...]:
        with self._lock:
            return tuple(
                self._envelope(row)
                for row in self._db.execute(
                    "SELECT * FROM items WHERE sequence IN (SELECT MIN(sequence) FROM items "
                    "WHERE receipt_json IS NULL GROUP BY conversation_id) ORDER BY sequence"
                )
            )

    def pending_count(self, conversation_id: str | None = None) -> int:
        with self._lock:
            query = "SELECT COUNT(*) FROM items WHERE receipt_json IS NULL"
            args = () if conversation_id is None else (conversation_id,)
            if conversation_id is not None:
                query += " AND conversation_id=?"
            return int(self._db.execute(query, args).fetchone()[0])

    def has_pending(self, conversation_id: str | None = None) -> bool:
        return self.pending_count(conversation_id) > 0

    def snapshots(self, submission_id: str) -> tuple[FileSnapshot, ...]:
        with self._lock:
            row = self.lookup(submission_id)
            if row is None or not row.pending:
                raise FileNotFoundError(submission_id)
            return tuple(
                FileSnapshot(m, self._blobs / m.sha256) for m in row.envelope.file_dependencies
            )

    def acknowledge(self, receipt: AcceptanceReceipt) -> None:
        with self._lock:
            row = self.lookup(receipt.submission_id)
            if row is None:
                return
            envelope = row.envelope
            if (receipt.sender_id, receipt.payload_sha256) != (
                self.sender_id,
                envelope.payload_sha256,
            ):
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

    async def _send_one(self, link: OutboxLink, envelope: QueuedEnvelope) -> None:
        while True:
            try:
                response = await link.request(WireMessageKind.envelope, self._object(envelope))
                if response.kind is WireMessageKind.missing_files:
                    from planner.executor.protocol import FileManifest

                    missing = type_adapter(tuple[FileManifest, ...]).validate_python(
                        response.payload.get("files")
                    )
                    snapshots = {
                        s.manifest: s
                        for s in await asyncio.to_thread(self.snapshots, envelope.submission_id)
                    }
                    for manifest in missing:
                        if manifest not in snapshots:
                            raise ValueError("VPS requested a file outside this item")
                        refusal = await link.put_file(envelope, snapshots[manifest])
                        if refusal is not None:
                            await asyncio.to_thread(self.acknowledge, refusal)
                            return
                    continue
                if response.kind is not WireMessageKind.receipt:
                    raise ValueError("expected outbox receipt")
                receipt = type_adapter(AcceptanceReceipt).validate_python(response.payload)
                await asyncio.to_thread(self.acknowledge, receipt)
                return
            except sqlite3.OperationalError as error:
                if error.sqlite_errorcode not in (
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                ):
                    raise
                await asyncio.sleep(OUTBOX_RETRY_SECONDS)

    async def send_pending(self, link: OutboxLink | Link) -> None:
        if isinstance(link, Link):
            link = _OutboxDelivery(self, link)
        self._loop = asyncio.get_running_loop()
        async with self._sending:
            tasks: dict[asyncio.Task[None], str] = {}
            wake: asyncio.Task[bool] | None = None

            try:
                while not self._closed:
                    self._wake.clear()
                    active = set(tasks.values())
                    for envelope in await asyncio.to_thread(self.pending_heads):
                        if len(tasks) >= MAX_TRANSFERS:
                            break
                        if envelope.conversation_id not in active:
                            tasks[asyncio.create_task(self._send_one(link, envelope))] = (
                                envelope.conversation_id
                            )
                            active.add(envelope.conversation_id)
                    wake = asyncio.create_task(self._wake.wait())
                    done, _ = await asyncio.wait(
                        (*tasks, wake), return_when=asyncio.FIRST_COMPLETED
                    )
                    wake.cancel()
                    await asyncio.gather(wake, return_exceptions=True)
                    for task in done:
                        if task is not wake:
                            finished = cast(asyncio.Task[None], task)
                            tasks.pop(finished)
                            await finished
            finally:
                if wake is not None:
                    wake.cancel()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, *([wake] if wake else []), return_exceptions=True)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self._database is not None:
                self._database.close()
            self._process_lock.close()
        self._notify()


class _OutboxDelivery:
    def __init__(self, outbox: MacOutbox, link: Link) -> None:
        self.outbox = outbox
        self.link = link

    async def request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        return await self.link.request(kind, payload)

    async def put_file(
        self, envelope: QueuedEnvelope, snapshot: FileSnapshot
    ) -> AcceptanceReceipt | None:
        link = self.link
        child = envelope.payload.get("child")
        if child is None:
            ask = cast(JsonObject, envelope.payload.get("ask", {}))
            child = ask.get("child")
        if child is None:
            raise ValueError("file upload lacks assigned child")
        self.outbox.transfers += 1
        try:
            async with link.transfer_capacity():
                request_id = uuid4().hex
                try:
                    response = await link.request(
                        WireMessageKind.put_file,
                        {
                            "manifest": as_object(snapshot.manifest),
                            "child": child,
                        },
                        request_id=request_id,
                    )
                    slot = type_adapter(FileTransfer).validate_python(response.payload)
                    if (
                        response.kind is not WireMessageKind.file_transfer
                        or slot.direction != "upload"
                        or slot.manifest != snapshot.manifest
                    ):
                        raise ValueError("invalid upload transfer")
                    await put_file(link, slot, snapshot.local_path)
                except ValueError as error:
                    if link.is_closing():
                        raise ConnectionError("file generation changed") from error
                    # This item cannot use the descriptor. Answer its request and
                    # obtain a durable refusal without disturbing other conversations.
                    refused = await link.request(
                        WireMessageKind.error,
                        as_object(EnvelopeRefusal(envelope, str(error))),
                        request_id=request_id,
                    )
                    if refused.kind is not WireMessageKind.receipt:
                        raise ValueError("expected upload refusal receipt") from error
                    return type_adapter(AcceptanceReceipt).validate_python(refused.payload)
                if link.is_closing():
                    raise ConnectionError("file generation changed")
                return None
        finally:
            self.outbox.transfers -= 1
