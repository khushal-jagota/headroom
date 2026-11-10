"""Apply Mac queue entries once, in order, with their files and receipt.

Items arrive in per-conversation batches (route 4). Each item is applied on its own,
with its effect and its receipt in one commit, and the batch stops at the first item
that must be sent again (retry later) or whose files are not uploaded yet. The sender
is fenced by being the current process, checked inside the receipt's transaction; an
item's authority is its child's assignment, not the process that sends it, so a new
process replays its predecessor's items while their assignment is open.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import sqlite3
from collections.abc import Callable, Coroutine
from functools import partial
from pathlib import Path
from typing import Any, Protocol, cast

from planner.conversation.backend_event_contracts import (
    AcceptedEvent,
    CommitParticipant,
    EventTaken,
)
from planner.conversation.backends.contracts import BackendEventValue, TurnToken
from planner.core.clock import Clock
from planner.core.config import MAC_ITEM_RETRY_LATER_SECONDS
from planner.core.contracts import ErrorCode, PlannerError, PrincipalKind
from planner.core.task_settlement import run_on_worker_thread, wait_for_settlement
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.configuration import MAX_ITEM_BATCH_ITEMS
from planner.executor.files import (
    contained_path,
    directory_descriptor,
    file_digest,
    install_verified,
    safe_component,
)
from planner.executor.protocol import (
    AcceptanceReceipt,
    AnswerApplication,
    BackendEvent,
    CommandOutcome,
    EnvelopeKind,
    FileManifest,
    FileScope,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    ItemBatchStop,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    as_object,
    decode_event_arguments,
    envelope_child,
    payload_child,
    type_adapter,
)
from planner.executor.store import ExecutorStore, VpsDatabase, _invalid, _record
from planner.executor.vps_agent_writes import AgentWrites
from planner.executor.vps_presence_store import require_current_incarnation_in_transaction
from planner.executor.vps_runs import MacRuns
from planner.stalls.contracts import TrackedWaitKind
from planner.stalls.registry import WAITS


class MacInboxConversations(Protocol):
    async def take_backend_event(
        self,
        turn: TurnToken | None,
        arguments: BackendEventValue,
        *,
        in_commit: CommitParticipant,
        conversation_id: str | None = None,
        retry_later_at: float | None = None,
    ) -> AcceptedEvent: ...

    async def fence_replaced_remote_child(
        self, reference: ChildReference, *, retry_later_at: float | None = None
    ) -> None: ...

    async def take_answer_application(
        self,
        turn: TurnToken,
        answer_id: str,
        provider_id: str,
        kind: str,
        in_commit: CommitParticipant,
        *,
        retry_later_at: float | None = None,
    ) -> AcceptedEvent: ...


class TransactionalWriter(Protocol):
    def __call__(self, connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        """Validate original authority and apply effect; never commit or do native I/O.

        Acceptor checks a matching receipt first, verifies order/files, then invokes
        this writer and inserts the first receipt in this same transaction. A
        permanent PlannerError is recorded under a savepoint; transient errors roll
        back without receipt. Async/native effects use their separate command fate.
        """
        ...


LOGGER = logging.getLogger(__name__)


def _observe_outcome(task: asyncio.Task[AcceptanceReceipt]) -> None:
    """Its caller may have answered retry later and gone; the next one reads its receipt."""
    if not task.cancelled():
        task.exception()


class MissingFiles(Exception):
    """The sender must upload these immutable bytes, then retry the same item."""

    def __init__(self, manifests: tuple[FileManifest, ...]) -> None:
        self.manifests = manifests
        super().__init__("File dependencies are not durably published")


class InboxStore(VpsDatabase):
    def __init__(self, db_path: str) -> None:
        super().__init__(db_path)
        self.runs = ExecutorStore(db_path)

    @staticmethod
    def _receipt(row: sqlite3.Row) -> AcceptanceReceipt:
        return type_adapter(AcceptanceReceipt).validate_json(row["result_json"])

    def accept(
        self,
        envelope: QueuedEnvelope,
        writer: TransactionalWriter,
        now_ms: int,
        *,
        identity: ExecutorIdentity,
        check_files: bool = True,
    ) -> AcceptanceReceipt:
        with self._transaction() as connection:
            receipt, _ = self.accept_in_transaction(
                connection, envelope, writer, now_ms, identity=identity, check_files=check_files
            )
            return receipt

    def accept_in_transaction(
        self,
        connection: sqlite3.Connection,
        envelope: QueuedEnvelope,
        writer: TransactionalWriter,
        now_ms: int,
        *,
        identity: ExecutorIdentity,
        check_files: bool = True,
    ) -> tuple[AcceptanceReceipt, bool]:
        """Apply once and record the receipt; the caller owns commit and publication."""
        if not connection.in_transaction:
            raise RuntimeError("inbox acceptance requires an open transaction")
        # The binding sender fence, in the transaction that writes the receipt: an item
        # from a replaced process is never receipted, so its successor still sends it.
        require_current_incarnation_in_transaction(connection, identity.executor_incarnation)
        previous = self._prior_receipt(connection, envelope, identity)
        if previous is not None:
            return previous, False
        disposition = ReceiptDisposition.accepted
        connection.execute("SAVEPOINT domain_write")
        try:
            self._validate_envelope(connection, envelope, check_files=check_files)
            if check_files:
                self.install_files(envelope.file_dependencies)
            result = writer(connection, envelope)
            if not connection.in_transaction:
                raise RuntimeError("Executor domain writer committed its transaction")
            connection.execute("RELEASE SAVEPOINT domain_write")
        except (PlannerError, ValueError, KeyError, TypeError) as error:
            connection.execute("ROLLBACK TO SAVEPOINT domain_write")
            connection.execute("RELEASE SAVEPOINT domain_write")
            disposition = ReceiptDisposition.refused
            refusal = error if isinstance(error, PlannerError) else _invalid(str(error))
            result = cast(JsonObject, refusal.to_payload())
        if disposition is ReceiptDisposition.accepted and result.get("fate") == "uncertain":
            disposition = ReceiptDisposition.uncertain
        selected = AcceptanceReceipt(
            envelope.sender_id,
            envelope.submission_id,
            disposition,
            result,
            now_ms,
        )
        self._write_position(connection, envelope, selected)
        return selected, disposition is not ReceiptDisposition.refused

    @staticmethod
    def _write_position(
        connection: sqlite3.Connection,
        envelope: QueuedEnvelope,
        receipt: AcceptanceReceipt,
        *,
        dispatching: bool = False,
    ) -> None:
        connection.execute(
            "INSERT INTO mac_inbox_positions VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(sender_id,conversation_id) DO UPDATE SET "
            "sequence=excluded.sequence,submission_id=excluded.submission_id,"
            "disposition=excluded.disposition,result_json=excluded.result_json",
            (
                envelope.sender_id,
                envelope.conversation_id,
                envelope.sequence,
                envelope.submission_id,
                "dispatching" if dispatching else receipt.disposition.value,
                _record(receipt),
            ),
        )

    def file_target(self, manifest: FileManifest) -> Path:
        safe_component(manifest.owner_id)
        if manifest.byte_count < 0 or not re.fullmatch(r"[0-9a-f]{64}", manifest.sha256):
            raise ValueError("invalid file length or digest")
        if manifest.scope is not FileScope.ticket:
            raise ValueError("Mac uploads must belong to a Ticket")
        root = Path(self.db_path).resolve().parent / "files" / "tickets"
        return contained_path(root, f"{manifest.owner_id}/{manifest.relative_path}")

    def _staged_file(self, manifest: FileManifest) -> Path:
        self.file_target(manifest)
        key = hashlib.sha256(_record(manifest).encode()).hexdigest()
        return Path(self.db_path).resolve().parent / "executor-uploads" / key

    @staticmethod
    def _matches(path: Path, manifest: FileManifest) -> bool:
        try:
            return file_digest(path) == (manifest.sha256, manifest.byte_count)
        except FileNotFoundError:
            return False

    def store_upload(self, manifest: FileManifest, source: Path) -> None:
        """Verify and flush into private staging, never into the served file tree."""
        target = self._staged_file(manifest)
        install_verified(source, target, manifest)

    def install_files(self, manifests: tuple[FileManifest, ...]) -> None:
        """Called under the receipt transaction, before any referenced effect."""
        for manifest in manifests:
            target = self.file_target(manifest)
            if self._matches(target, manifest):
                continue  # Rename survived a crash before the receipt committed.
            source = self._staged_file(manifest)
            if not self._matches(source, manifest):
                raise MissingFiles((manifest,))
            with directory_descriptor(target.parent, create=True) as parent:
                with directory_descriptor(source.parent) as staging:
                    os.replace(source.name, target.name, src_dir_fd=staging, dst_dir_fd=parent)
                    os.fsync(staging)
                os.fsync(parent)

    def preflight(
        self, envelope: QueuedEnvelope, *, identity: ExecutorIdentity
    ) -> AcceptanceReceipt | None:
        """Validate prior result/order/files before a separately serialized native act.

        This grants no accepted receipt. The endpoint retains the per-conversation
        lock through native outcome recording and must preserve sender dedup IDs.
        """
        with self._transaction() as connection:
            previous = self._prior_receipt(connection, envelope, identity)
            if previous is None:
                self._validate_envelope(connection, envelope)
            return previous

    def _prior_receipt(
        self, connection: sqlite3.Connection, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt | None:
        """Look up the sender's position. Nothing here reads a connection.

        The sender is fenced by its process (the current incarnation) in the writing
        transaction. A dropped request of the same process fails nothing: the resend is
        the same item, and its sequence decides whether it is new.
        """
        previous = connection.execute(
            "SELECT * FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?",
            (envelope.sender_id, envelope.conversation_id),
        ).fetchone()
        if previous is None or envelope.sequence > previous["sequence"]:
            return None
        stored = self._receipt(previous)
        if envelope.sequence == previous["sequence"]:
            if stored.submission_id != envelope.submission_id:
                raise _invalid("Submission identity conflicts with its first receipt")
            return stored
        # Old sequences are acknowledged without rerunning effects or regressing position.
        return AcceptanceReceipt(
            envelope.sender_id,
            envelope.submission_id,
            ReceiptDisposition.accepted,
            {},
            stored.accepted_at_vps_ms,
        )

    def _validate_envelope(
        self, connection: sqlite3.Connection, envelope: QueuedEnvelope, *, check_files: bool = True
    ) -> None:
        if check_files and envelope.file_dependencies:
            reference = type_adapter(ChildReference).validate_python(
                payload_child(envelope.payload)
            )
            assignment = self.runs.open_child_assignment(connection, reference)
            if assignment is None and envelope.kind in (
                EnvelopeKind.backend_event,
                EnvelopeKind.cli_write,
            ):
                raise PlannerError(ErrorCode.agent_forbidden, "child has no VPS assignment", {})
            for manifest in envelope.file_dependencies:
                self.file_target(manifest)
                if (
                    reference.conversation_id != envelope.conversation_id
                    or assignment is None
                    or assignment != ("ticket", manifest.owner_id)
                ):
                    raise _invalid("artifact belongs to another Ticket")
            missing = tuple(
                m
                for m in envelope.file_dependencies
                if not self._matches(self.file_target(m), m)
                and not self._matches(self._staged_file(m), m)
            )
            if missing:
                raise MissingFiles(missing)


class MacInbox:
    def __init__(self, db_path: str, clock: Clock, runs: MacRuns) -> None:
        self.db_path = db_path
        self.clock = clock
        self.runs = runs
        self.store = InboxStore(db_path)
        self.conversations: MacInboxConversations
        self.agent_writes = AgentWrites(self.store, clock, runs)
        self._conversation_locks: dict[str, asyncio.Lock] = {}
        # An item whose answer ran out of time while its work could not stop part-way:
        # a message being delivered, or a write under way. By conversation.
        self._still_settling: dict[str, asyncio.Task[AcceptanceReceipt]] = {}

    async def accept_batch(
        self, conversation_id: str, batch: ItemBatch, identity: ExecutorIdentity
    ) -> ItemBatchReply:
        """Apply a batch item by item, within one MAC_ITEM_RETRY_LATER_SECONDS budget.

        The reply holds the receipts of a prefix of the batch, and where it stopped: at the
        first item to send again, or the first whose files must be uploaded first.
        """
        _validate_batch(conversation_id, batch)
        retry_later_at = asyncio.get_running_loop().time() + MAC_ITEM_RETRY_LATER_SECONDS
        receipts: list[AcceptanceReceipt] = []
        for item in batch.items:
            envelope = item.envelope
            try:
                if item.sender_refusal is not None:
                    receipt = await self.refuse_item(
                        envelope, identity, item.sender_refusal, retry_later_at=retry_later_at
                    )
                else:
                    receipt = await self.accept(envelope, identity, retry_later_at=retry_later_at)
            except MissingFiles as missing:
                return ItemBatchReply(
                    tuple(receipts),
                    ItemBatchStop(
                        envelope.sequence, "missing_files", str(missing), missing.manifests
                    ),
                )
            except (sqlite3.OperationalError, OSError) as error:
                # Busy or failing storage before the item's own write: nothing was saved.
                return ItemBatchReply(
                    tuple(receipts),
                    ItemBatchStop(
                        envelope.sequence,
                        "retry_later",
                        f"The VPS could not save this yet: {error}",
                    ),
                )
            except PlannerError as error:
                if error.code is ErrorCode.executor_incarnation_not_current and receipts:
                    # Replaced part-way: keep what was saved; the next request is refused.
                    error = PlannerError(ErrorCode.retry_later, error.message)
                if error.code is not ErrorCode.retry_later:
                    raise
                return ItemBatchReply(
                    tuple(receipts), ItemBatchStop(envelope.sequence, "retry_later", error.message)
                )
            receipts.append(receipt)
        return ItemBatchReply(tuple(receipts))

    def authorize_upload(self, upload: FileUploadRequest) -> None:
        principal = self.runs.authority(upload.child)
        self.store.file_target(upload.manifest)
        if principal.kind is not PrincipalKind.ticket or upload.manifest.owner_id != principal.id:
            raise PlannerError(ErrorCode.agent_forbidden, "artifact belongs to another Ticket", {})

    def store_upload(self, upload: FileUploadRequest, source: Path) -> None:
        self.authorize_upload(upload)
        self.store.store_upload(upload.manifest, source)

    def _require_application_assignment(
        self, application: AnswerApplication, conversation_id: str
    ) -> None:
        """Authority is the child's open assignment, whichever process sends the item: a
        new process replays its predecessor's applications while the assignment is open.
        Only a child_stopped item may use a closed assignment."""
        child = application.ask.child
        if (
            child.conversation_id != conversation_id
            or self.runs.store.child_assignment(child) is None
        ):
            raise ValueError("application lacks the original authenticated child assignment")

    async def accept(
        self,
        envelope: QueuedEnvelope,
        identity: ExecutorIdentity,
        *,
        retry_later_at: float | None = None,
    ) -> AcceptanceReceipt:
        """Answer accepted, refused, or retry later, by the loop time retry_later_at
        (MAC_ITEM_RETRY_LATER_SECONDS from now, unless a batch shares its budget).

        Retry later is a PlannerError with ErrorCode.retry_later: no receipt and no
        position are written, so the Mac's outbox still holds the item and sends it
        again. Its conversation's items are sent in order, so the order holds.
        """
        if retry_later_at is None:
            retry_later_at = asyncio.get_running_loop().time() + MAC_ITEM_RETRY_LATER_SECONDS
        return await self._answer_by(
            envelope,
            retry_later_at,
            partial(self._accept_envelope, envelope, identity, retry_later_at),
        )

    async def _answer_by(
        self,
        envelope: QueuedEnvelope,
        retry_later_at: float,
        work: Callable[[], Coroutine[Any, Any, AcceptanceReceipt]],
    ) -> AcceptanceReceipt:
        """Do one item of this conversation, answering it by the loop time retry_later_at.

        The inbox lock lets one item of a conversation in at a time. The work runs as
        its own task, because some of it cannot stop part-way: a message delivered to
        another conversation, a write already under way. Past the limit the answer is
        retry later and that task goes on. Until it ends, every next item of this
        conversation, its own resend included, is answered retry later too, so nothing
        overtakes it and nothing runs twice. Once it ends, the resend finds its receipt.
        """
        loop = asyncio.get_running_loop()
        conversation_id = envelope.conversation_id
        lock = self._conversation_locks.setdefault(conversation_id, asyncio.Lock())

        async def tracked() -> AcceptanceReceipt:
            # Seen by the stall checker while the item is saving, past its answer too.
            with WAITS.track(
                TrackedWaitKind.mac_item_still_saving,
                f"{conversation_id}:{envelope.sender_id}:{envelope.sequence}",
                detail=f"Saving Mac item {envelope.sequence}",
                conversation_id=conversation_id,
            ):
                return await work()

        try:
            async with asyncio.timeout_at(retry_later_at):
                await lock.acquire()
        except TimeoutError as error:
            raise PlannerError(
                ErrorCode.retry_later,
                "An earlier item of this conversation is still being saved",
            ) from error
        try:
            earlier = self._still_settling.get(conversation_id)
            if earlier is not None:
                await asyncio.wait({earlier}, timeout=max(retry_later_at - loop.time(), 0))
                if not earlier.done():
                    raise PlannerError(
                        ErrorCode.retry_later,
                        "An earlier attempt at this conversation's item is still settling",
                    )
                self._still_settling.pop(conversation_id, None)
            task = asyncio.create_task(tracked())
            task.add_done_callback(_observe_outcome)
            try:
                await asyncio.wait({task}, timeout=max(retry_later_at - loop.time(), 0))
            except asyncio.CancelledError:
                # A caller that is cancelled (a closing link, shutdown) cancels the work
                # and keeps this conversation until its blocking units settle, as before.
                task.cancel()
                await wait_for_settlement(task)
                raise
            if not task.done():
                self._still_settling[conversation_id] = task
                task.add_done_callback(
                    lambda done: (
                        self._still_settling.pop(conversation_id, None)
                        if self._still_settling.get(conversation_id) is done
                        else None
                    )
                )
                raise PlannerError(
                    ErrorCode.retry_later, "This item is still being saved; send it again"
                )
            try:
                return task.result()
            except (sqlite3.OperationalError, OSError) as error:
                # Busy or failing storage outside the conversation's own write: nothing
                # was saved, so the same item is simply sent again.
                raise PlannerError(
                    ErrorCode.retry_later, f"The VPS could not save this item yet: {error}"
                ) from error
        finally:
            lock.release()

    async def refuse_item(
        self,
        envelope: QueuedEnvelope,
        identity: ExecutorIdentity,
        message: str,
        *,
        retry_later_at: float | None = None,
    ) -> AcceptanceReceipt:
        """Record the sender's permanent failure to prepare an item as its refused receipt.

        Its authority is the child's assignment, not the sending process. Without that
        authority the item is still refused: the receipt says why, and nothing applies.
        """
        child = type_adapter(ChildReference).validate_python(envelope_child(envelope.payload))

        def authorize() -> None:
            if child.conversation_id != envelope.conversation_id:
                raise PlannerError(ErrorCode.validation, "item refusal belongs to another child")
            principal = self.runs.authority(child)
            for manifest in envelope.file_dependencies:
                self.store.file_target(manifest)
                if principal.kind is not PrincipalKind.ticket or principal.id != manifest.owner_id:
                    raise PlannerError(
                        ErrorCode.agent_forbidden, "upload refusal belongs to another Ticket"
                    )

        refusal = PlannerError(ErrorCode.validation, message)
        try:
            await run_on_worker_thread(authorize)
        except (PlannerError, ValueError) as error:
            refusal = (
                error
                if isinstance(error, PlannerError)
                else PlannerError(ErrorCode.validation, str(error))
            )

        async def refuse() -> AcceptanceReceipt:
            return await self._record_refusal(envelope, identity, refusal)

        return await self._answer_by(
            envelope,
            asyncio.get_running_loop().time() + MAC_ITEM_RETRY_LATER_SECONDS
            if retry_later_at is None
            else retry_later_at,
            refuse,
        )

    async def _accept_envelope(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity, retry_later_at: float
    ) -> AcceptanceReceipt:
        try:
            previous = await run_on_worker_thread(self.store.preflight, envelope, identity=identity)
            if previous is not None:
                if (
                    envelope.kind is EnvelopeKind.child_stopped
                    and previous.disposition is ReceiptDisposition.accepted
                    and previous.result.get("recorded") is True
                ):
                    await self.runs.accept_stop_receipt(
                        type_adapter(CommandOutcome).validate_python(envelope.payload),
                        previous,
                    )
                return previous
            if envelope.kind is EnvelopeKind.backend_event:
                return await self._apply_event(envelope, identity, retry_later_at)
            if envelope.kind is EnvelopeKind.answer_application:
                return await self._apply_answer(envelope, identity, retry_later_at)
            handler = {
                EnvelopeKind.cli_write: self.agent_writes.accept,
                EnvelopeKind.child_stopped: self._apply_stop,
            }[envelope.kind]
            return await handler(envelope, identity)
        except PlannerError as error:
            if error.code in (ErrorCode.retry_later, ErrorCode.executor_incarnation_not_current):
                # Not a refusal: a refusal receipt would drop the item for good. A
                # replaced process gets no receipt; its successor still holds the item.
                raise
            refusal = error
        except (ValueError, KeyError, TypeError) as error:
            refusal = PlannerError(ErrorCode.validation, str(error))
        except (MissingFiles, sqlite3.OperationalError, OSError):
            raise  # The sender uploads, or sends the same item again.
        except Exception as error:
            # It would fail the same way every time. Retrying a poison item forever
            # holds its whole conversation, so it is refused, and the refusal says why.
            LOGGER.error(
                "Saving a Mac item failed, so it is refused unless already recorded: "
                "conversation=%s sender=%s "
                "sequence=%d submission=%s kind=%s: %r",
                envelope.conversation_id,
                envelope.sender_id,
                envelope.sequence,
                envelope.submission_id,
                envelope.kind.value,
                error,
                exc_info=True,
            )
            refusal = PlannerError(
                ErrorCode.save_failed, f"The VPS could not save this item: {error!r}"
            )
        return await self._record_refusal(envelope, identity, refusal)

    async def _record_refusal(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity, refusal: PlannerError
    ) -> AcceptanceReceipt:
        def refuse(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
            raise refusal

        return await run_on_worker_thread(
            self.store.accept, envelope, refuse, now_ms(), identity=identity, check_files=False
        )

    def _record_event(
        self,
        envelope: QueuedEnvelope,
        identity: ExecutorIdentity,
        connection: sqlite3.Connection,
        take: EventTaken,
    ) -> AcceptedEvent:
        receipt, taken = self.store.accept_in_transaction(
            connection,
            envelope,
            lambda conn, row: take(),
            now_ms(),
            identity=identity,
        )
        return AcceptedEvent(as_object(receipt), taken)

    async def _apply_event(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity, retry_later_at: float
    ) -> AcceptanceReceipt:
        event = type_adapter(BackendEvent).validate_python(envelope.payload)
        await run_on_worker_thread(self.runs.authority, event.child)
        if event.child.conversation_id != envelope.conversation_id:
            raise ValueError("event conversation conflicts with envelope")

        if event.child.executor_incarnation != identity.executor_incarnation:
            # Replayed for a replaced process: a turn it ends must not hand the next
            # message to that child.
            await self.conversations.fence_replaced_remote_child(
                event.child, retry_later_at=retry_later_at
            )
        decoded = decode_event_arguments(event)
        result = await self.conversations.take_backend_event(
            event.turn,
            decoded,
            in_commit=partial(self._record_event, envelope, identity),
            conversation_id=event.child.conversation_id,
            retry_later_at=retry_later_at,
        )
        return type_adapter(AcceptanceReceipt).validate_python(result.result)

    async def _apply_stop(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        outcome = type_adapter(CommandOutcome).validate_python(envelope.payload)
        if outcome.child.conversation_id != envelope.conversation_id:
            raise ValueError("command outcome conversation conflicts with envelope")
        await run_on_worker_thread(self.runs.validate_stop, outcome)
        receipt = await run_on_worker_thread(
            self.store.accept,
            envelope,
            lambda conn, row: cast(
                JsonObject,
                {
                    "recorded": True,
                    "stop_command_id": outcome.command_id,
                    "stopped_child": as_object(outcome.child),
                },
            ),
            now_ms(),
            identity=identity,
        )
        if receipt.disposition is ReceiptDisposition.accepted:
            await self.runs.accept_stop_receipt(outcome, receipt)
        return receipt

    async def _apply_answer(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity, retry_later_at: float
    ) -> AcceptanceReceipt:
        application = type_adapter(AnswerApplication).validate_python(envelope.payload)
        await run_on_worker_thread(
            self._require_application_assignment, application, envelope.conversation_id
        )
        return type_adapter(AcceptanceReceipt).validate_python(
            (
                await self.conversations.take_answer_application(
                    application.ask.turn,
                    application.answer_id,
                    application.ask.ask_id,
                    application.ask.kind.value,
                    partial(self._record_event, envelope, identity),
                    retry_later_at=retry_later_at,
                )
            ).result
        )


def _is_plain_event(envelope: QueuedEnvelope) -> bool:
    return envelope.kind is EnvelopeKind.backend_event and not envelope.file_dependencies


def _validate_batch(conversation_id: str, batch: ItemBatch) -> None:
    """One conversation's items, ascending, from the batch's sender. Only the last item
    may be other than a plain backend event, so a resend after a lost reply gets that
    item's real stored receipt (earlier plain events get the synthetic one)."""
    items = batch.items
    if not 1 <= len(items) <= MAX_ITEM_BATCH_ITEMS:
        raise PlannerError(ErrorCode.validation, "an item batch holds 1 to 32 items")
    previous: int | None = None
    for index, item in enumerate(items):
        envelope = item.envelope
        if envelope.conversation_id != conversation_id or envelope.sender_id != batch.sender_id:
            raise PlannerError(ErrorCode.validation, "a batch item belongs to another sender")
        if previous is not None and envelope.sequence <= previous:
            raise PlannerError(ErrorCode.validation, "batch items must ascend by sequence")
        previous = envelope.sequence
        if index < len(items) - 1 and not _is_plain_event(envelope):
            raise PlannerError(
                ErrorCode.validation, "a batch ends after its first item that is not a plain event"
            )
