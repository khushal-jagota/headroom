"""Apply Mac queue entries once, in order, with their files and receipt."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import sqlite3
from functools import partial
from pathlib import Path
from typing import cast

from planner.conversation.backend_event_contracts import AcceptedEvent, EventTaken
from planner.core.clock import Clock
from planner.core.contracts import ErrorCode, PlannerError, PrincipalKind
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.clock import now_ms
from planner.executor.files import (
    contained_path,
    directory_descriptor,
    file_digest,
    install_verified,
    safe_component,
)
from planner.executor.link_files import GenerationTransfers, settled_io
from planner.executor.protocol import (
    AcceptanceReceipt,
    AnswerApplication,
    BackendEvent,
    CommandOutcome,
    EnvelopeKind,
    EnvelopeRefusal,
    FileManifest,
    FileScope,
    FileUploadRequest,
    JsonObject,
    MacInboxConversations,
    QueuedEnvelope,
    ReceiptDisposition,
    TransactionalWriter,
    as_object,
    decode_event_arguments,
    envelope_child,
    envelope_payload_digest,
    payload_child,
    type_adapter,
)
from planner.executor.store import ExecutorStore, VpsDatabase, _invalid, _record
from planner.executor.vps_agent_writes import AgentWrites
from planner.executor.vps_runs import MacRuns


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
        if disposition is ReceiptDisposition.refused and envelope.kind is EnvelopeKind.cli_write:
            from planner.executor.refusals import record_refused_write

            record_refused_write(connection, self.db_path, envelope, result)
        selected = AcceptanceReceipt(
            envelope.sender_id,
            envelope.submission_id,
            envelope.payload_sha256,
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
        self.runs._connected(connection, identity)
        previous = connection.execute(
            "SELECT * FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?",
            (envelope.sender_id, envelope.conversation_id),
        ).fetchone()
        if previous is None or envelope.sequence > previous["sequence"]:
            return None
        stored = self._receipt(previous)
        if envelope.sequence == previous["sequence"]:
            if (stored.submission_id, stored.payload_sha256) != (
                envelope.submission_id,
                envelope.payload_sha256,
            ):
                raise _invalid("Submission identity conflicts with its first receipt")
            return stored
        # Old sequences are acknowledged without rerunning effects or regressing position.
        return AcceptanceReceipt(
            envelope.sender_id,
            envelope.submission_id,
            envelope.payload_sha256,
            ReceiptDisposition.accepted,
            {},
            stored.accepted_at_vps_ms,
        )

    def _validate_envelope(
        self, connection: sqlite3.Connection, envelope: QueuedEnvelope, *, check_files: bool = True
    ) -> None:
        if envelope.payload_sha256 != envelope_payload_digest(
            envelope.payload,
            envelope.file_dependencies,
            sender_id=envelope.sender_id,
            conversation_id=envelope.conversation_id,
            sequence=envelope.sequence,
            submission_id=envelope.submission_id,
            kind=envelope.kind,
        ):
            raise _invalid("Envelope digest does not match content")
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

    def authorize_upload(self, upload: FileUploadRequest) -> None:
        principal = self.runs.authority(upload.child)
        self.store.file_target(upload.manifest)
        if principal.kind is not PrincipalKind.ticket or upload.manifest.owner_id != principal.id:
            raise PlannerError(ErrorCode.agent_forbidden, "artifact belongs to another Ticket", {})

    def store_upload(self, upload: FileUploadRequest, source: Path) -> None:
        self.authorize_upload(upload)
        self.store.store_upload(upload.manifest, source)

    def _require_application_assignment(
        self, application: AnswerApplication, identity: ExecutorIdentity, conversation_id: str
    ) -> None:
        child = application.ask.child
        if (
            child.executor_incarnation != identity.executor_incarnation
            or child.conversation_id != conversation_id
            or (
                self.runs.store.child_assignment(child) is None
                and not self.runs.store.has_closed_child_assignment(child)
            )
        ):
            raise ValueError("application lacks the original authenticated child assignment")

    async def accept(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        lock = self._conversation_locks.setdefault(envelope.conversation_id, asyncio.Lock())
        async with lock:
            receipt = await self._accept_envelope(
                envelope,
                identity,
            )
            return receipt

    async def refuse_item(
        self,
        rejected: EnvelopeRefusal,
        identity: ExecutorIdentity,
        request_id: str,
        transfers: GenerationTransfers,
    ) -> AcceptanceReceipt:
        envelope = rejected.envelope
        child = type_adapter(ChildReference).validate_python(envelope_child(envelope.payload))
        if child.executor_incarnation != identity.executor_incarnation or (
            child.conversation_id != envelope.conversation_id
        ):
            raise ValueError("upload refusal belongs to another child")

        def authorize() -> None:
            principal = self.runs.authority(child)
            for manifest in envelope.file_dependencies:
                self.store.file_target(manifest)
                if principal.kind is not PrincipalKind.ticket or principal.id != manifest.owner_id:
                    raise ValueError("upload refusal belongs to another Ticket")

        await settled_io(authorize)
        # The request identifies this reservation, not another conversation's bytes.
        for transfer_id, (reserved_request_id, upload) in tuple(transfers.uploads.items()):
            if reserved_request_id == request_id:
                if upload.child != child or upload.manifest not in envelope.file_dependencies:
                    raise ValueError("upload refusal does not belong to this item")
                transfers.uploads.pop(transfer_id)
                transfers.release(transfer_id)
        lock = self._conversation_locks.setdefault(envelope.conversation_id, asyncio.Lock())
        async with lock:
            return await self._record_refusal(
                envelope, identity, PlannerError(ErrorCode.validation, rejected.error_message)
            )

    async def _accept_envelope(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        try:
            previous = await settled_io(partial(self.store.preflight, envelope, identity=identity))
            if previous is not None:
                if (
                    envelope.kind is EnvelopeKind.child_stopped
                    and previous.disposition is ReceiptDisposition.accepted
                    and previous.result.get("recorded") is True
                ):
                    self.runs._resolve_command(
                        type_adapter(CommandOutcome).validate_python(envelope.payload),
                        stop_proven=True,
                    )
                return previous
            handler = {
                EnvelopeKind.backend_event: self._apply_event,
                EnvelopeKind.cli_write: self.agent_writes.accept,
                EnvelopeKind.child_stopped: self._apply_stop,
                EnvelopeKind.answer_application: self._apply_answer,
            }[envelope.kind]
            return await handler(envelope, identity)
        except PlannerError as error:
            refusal = error
        except (ValueError, KeyError, TypeError) as error:
            refusal = PlannerError(ErrorCode.validation, str(error))
        return await self._record_refusal(envelope, identity, refusal)

    async def _record_refusal(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity, refusal: PlannerError
    ) -> AcceptanceReceipt:
        def refuse(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
            raise refusal

        return await settled_io(
            partial(
                self.store.accept, envelope, refuse, now_ms(), identity=identity, check_files=False
            )
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
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        event = type_adapter(BackendEvent).validate_python(envelope.payload)
        await settled_io(self.runs.authority, event.child)
        if event.child.conversation_id != envelope.conversation_id:
            raise ValueError("event conversation conflicts with envelope")

        decoded = decode_event_arguments(event)
        result = await self.conversations.take_backend_event(
            event.turn,
            decoded,
            in_commit=partial(self._record_event, envelope, identity),
            conversation_id=event.child.conversation_id,
        )
        proxy = self.runs.children.get(event.child.child_id)
        if result.taken and proxy is not None and event.turn is not None:
            from planner.conversation.backends.contracts import (
                PermissionAskRaised,
                UserInputRequested,
            )

            if isinstance(decoded, PermissionAskRaised):
                proxy.observe_callback(
                    AskReference(event.child, event.turn, decoded.ask.ask_id, AskKind.permission)
                )
            elif isinstance(decoded, UserInputRequested):
                proxy.observe_callback(
                    AskReference(
                        event.child, event.turn, decoded.request.request_id, AskKind.user_input
                    )
                )
        return type_adapter(AcceptanceReceipt).validate_python(result.result)

    async def _apply_stop(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        outcome = type_adapter(CommandOutcome).validate_python(envelope.payload)
        if outcome.child.conversation_id != envelope.conversation_id:
            raise ValueError("command outcome conversation conflicts with envelope")
        await settled_io(self.runs.validate_stop, outcome)
        receipt = await settled_io(
            partial(
                self.store.accept,
                envelope,
                lambda conn, row: cast(JsonObject, {"recorded": True}),
                now_ms(),
                identity=identity,
            )
        )
        if receipt.disposition is ReceiptDisposition.accepted:
            self.runs._resolve_command(outcome, stop_proven=True)
        return receipt

    async def _apply_answer(
        self, envelope: QueuedEnvelope, identity: ExecutorIdentity
    ) -> AcceptanceReceipt:
        application = type_adapter(AnswerApplication).validate_python(envelope.payload)
        await settled_io(
            self._require_application_assignment,
            application,
            identity,
            envelope.conversation_id,
        )
        return type_adapter(AcceptanceReceipt).validate_python(
            (
                await self.conversations.take_answer_application(
                    application.ask.turn,
                    application.answer_id,
                    application.ask.ask_id,
                    application.ask.kind.value,
                    partial(self._record_event, envelope, identity),
                )
            ).result
        )
