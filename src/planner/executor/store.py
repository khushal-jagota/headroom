"""One authoritative VPS store; no outbound answer queue or Mac state replica."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from typing import Any, cast
from uuid import uuid4

from pydantic import TypeAdapter

from planner.conversation.backends.contracts import TurnToken
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AcceptanceReceipt,
    AnswerActionTiming,
    AnswerApplication,
    AskEvidence,
    AskReachability,
    AskReference,
    AuthorizedAction,
    ChildReference,
    ClockSample,
    CommandOutcome,
    ConnectionState,
    DurableAnswer,
    ExecutorIdentity,
    FileManifest,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    ReconciliationInventory,
    ReconciliationResult,
    TransactionalWriter,
)
from planner.executor.logic.reconciliation import answer_action_timing, callback_reachability


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record(value: Any) -> str:
    return _json(asdict(value))


def _parse[T](model: type[T], value: str) -> T:
    return TypeAdapter(model).validate_json(value)


def _ask_key(ask: AskReference) -> str:
    return hashlib.sha256(_record(ask).encode()).hexdigest()


def _invalid(message: str) -> PlannerError:
    return PlannerError(ErrorCode.validation, message)


class ExecutorStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def lookup_receipt(self, sender_id: str, submission_id: str) -> AcceptanceReceipt | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_receipts WHERE sender_id=? AND submission_id=?",
                (sender_id, submission_id),
            ).fetchone()
            return self._receipt(row) if row else None
        finally:
            connection.close()

    @staticmethod
    def _receipt(row: sqlite3.Row) -> AcceptanceReceipt:
        return AcceptanceReceipt(
            sender_id=row["sender_id"],
            submission_id=row["submission_id"],
            payload_sha256=row["payload_sha256"],
            disposition=ReceiptDisposition(row["disposition"]),
            result=cast(JsonObject, json.loads(row["result_json"])),
            accepted_at_vps_ms=row["accepted_at_vps_ms"],
        )

    def accept(
        self,
        envelope: QueuedEnvelope,
        writer: TransactionalWriter,
        now_ms: int,
        *,
        identity: ExecutorIdentity,
    ) -> AcceptanceReceipt:
        with self._transaction() as connection:
            previous = self._preflight(connection, envelope, identity)
            if previous is not None:
                return previous
            disposition = ReceiptDisposition.accepted
            connection.execute("SAVEPOINT domain_write")
            try:
                result = writer(connection, envelope)
                if not connection.in_transaction:
                    raise RuntimeError("Executor domain writer committed its transaction")
                connection.execute("RELEASE SAVEPOINT domain_write")
            except PlannerError as error:
                if error.code == ErrorCode.gateway_offline:
                    raise
                connection.execute("ROLLBACK TO SAVEPOINT domain_write")
                connection.execute("RELEASE SAVEPOINT domain_write")
                disposition = ReceiptDisposition.refused
                result = cast(JsonObject, error.to_payload())
            connection.execute(
                "INSERT INTO executor_receipts VALUES (?,?,?,?,?,?,?,?)",
                (
                    envelope.sender_id,
                    envelope.submission_id,
                    envelope.conversation_id,
                    envelope.sequence,
                    envelope.payload_sha256,
                    disposition.value,
                    _json(result),
                    now_ms,
                ),
            )
            return AcceptanceReceipt(
                envelope.sender_id,
                envelope.submission_id,
                envelope.payload_sha256,
                disposition,
                result,
                now_ms,
            )

    def begin_connection(self, identity: ExecutorIdentity, now_ms: int) -> None:
        if (
            identity.protocol_version != PROTOCOL_VERSION
            or identity.spool_format_version != SPOOL_FORMAT_VERSION
            or identity.connection_generation < 1
        ):
            raise _invalid("Executor protocol or spool format is incompatible")
        with self._transaction() as connection:
            current = connection.execute(
                "SELECT * FROM executor_connections WHERE active=1"
            ).fetchone()
            seen = connection.execute(
                "SELECT * FROM executor_connections WHERE executor_incarnation=?",
                (identity.executor_incarnation,),
            ).fetchone()
            if seen is not None and (
                not seen["active"]
                or identity.connection_generation <= seen["connection_generation"]
            ):
                raise _invalid("Executor connection generation is stale")
            if current is not None:
                connection.execute("UPDATE executor_connections SET active=0 WHERE active=1")
            connection.execute(
                "INSERT INTO executor_connections VALUES (?,?,?,?,?,?,1) "
                "ON CONFLICT(executor_incarnation) DO UPDATE SET "
                "connection_generation=excluded.connection_generation, "
                "identity_json=excluded.identity_json,connection_state=excluded.connection_state, "
                "observed_at_ms=excluded.observed_at_ms,lost_at_ms=NULL,active=1",
                (
                    identity.executor_incarnation,
                    identity.connection_generation,
                    _record(identity),
                    ConnectionState.reconciling.value,
                    now_ms,
                    None,
                ),
            )
            connection.execute(
                "UPDATE executor_asks SET reachability=?,observed_at_ms=?",
                (AskReachability.uncertain.value, now_ms),
            )

    @staticmethod
    def _current(connection: sqlite3.Connection, identity: ExecutorIdentity) -> sqlite3.Row:
        current = connection.execute("SELECT * FROM executor_connections WHERE active=1").fetchone()
        if current is None or current["identity_json"] != _record(identity):
            raise _invalid("Executor connection generation is stale")
        return cast(sqlite3.Row, current)

    @classmethod
    def _connected(cls, connection: sqlite3.Connection, identity: ExecutorIdentity) -> sqlite3.Row:
        current = cls._current(connection, identity)
        if current["connection_state"] not in (
            ConnectionState.reconciling.value,
            ConnectionState.ready.value,
        ):
            raise _invalid("Executor connection is unavailable")
        return current

    def mark_ready(self, identity: ExecutorIdentity, now_ms: int) -> None:
        with self._transaction() as connection:
            self._connected(connection, identity)
            connection.execute(
                "UPDATE executor_connections SET connection_state=?,observed_at_ms=? "
                "WHERE active=1",
                (ConnectionState.ready.value, now_ms),
            )

    def mark_lost(self, identity: ExecutorIdentity, now_ms: int) -> None:
        with self._transaction() as connection:
            # A late close from an old socket cannot invalidate the replacement.
            row = connection.execute(
                "SELECT identity_json FROM executor_connections WHERE active=1"
            ).fetchone()
            if row is None or row[0] != _record(identity):
                return
            connection.execute(
                "UPDATE executor_connections SET connection_state=?,"
                "lost_at_ms=COALESCE(lost_at_ms,?),observed_at_ms=? "
                "WHERE active=1",
                (ConnectionState.disconnected.value, now_ms, now_ms),
            )
            connection.execute(
                "UPDATE executor_asks SET reachability=?,observed_at_ms=?",
                (AskReachability.uncertain.value, now_ms),
            )

    def register_child(
        self, child: ChildReference, principal_kind: str, principal_id: str, now_ms: int
    ) -> None:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM executor_child_assignments "
                "WHERE executor_incarnation=? AND child_id=?",
                (child.executor_incarnation, child.child_id),
            ).fetchone()
            if existing:
                if (
                    existing["conversation_id"],
                    existing["principal_kind"],
                    existing["principal_id"],
                ) != (child.conversation_id, principal_kind, principal_id):
                    raise _invalid("Child assignment identity conflicts")
                return
            connection.execute(
                "INSERT INTO executor_child_assignments VALUES (?,?,?,?,?,?,NULL)",
                (
                    child.executor_incarnation,
                    child.child_id,
                    child.conversation_id,
                    principal_kind,
                    principal_id,
                    now_ms,
                ),
            )

    def child_assignment(self, child: ChildReference) -> tuple[str, str] | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT principal_kind,principal_id FROM executor_child_assignments "
                "WHERE executor_incarnation=? AND child_id=? AND conversation_id=? "
                "AND closed_at_ms IS NULL",
                (child.executor_incarnation, child.child_id, child.conversation_id),
            ).fetchone()
            return (row[0], row[1]) if row else None
        finally:
            connection.close()

    def close_child(self, child: ChildReference, now_ms: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                "UPDATE executor_child_assignments SET closed_at_ms=? "
                "WHERE executor_incarnation=? AND child_id=? AND conversation_id=?",
                (now_ms, child.executor_incarnation, child.child_id, child.conversation_id),
            )
            connection.execute(
                "UPDATE executor_asks SET reachability=?,observed_at_ms=? "
                "WHERE executor_incarnation=? AND child_id=?",
                (
                    AskReachability.child_gone.value,
                    now_ms,
                    child.executor_incarnation,
                    child.child_id,
                ),
            )

    def publish_ask(self, ask: AskReference, option_ids: tuple[str, ...], now_ms: int) -> None:
        with self._transaction() as connection:
            self.publish_ask_in_transaction(connection, ask, option_ids, now_ms)

    @staticmethod
    def publish_ask_in_transaction(
        connection: sqlite3.Connection, ask: AskReference, option_ids: tuple[str, ...], now_ms: int
    ) -> None:
        if ask.turn.conversation_id != ask.child.conversation_id or not option_ids:
            raise _invalid("Ask identity or options are invalid")
        previous = connection.execute(
            "SELECT option_ids_json FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
        ).fetchone()
        if previous is not None:
            if previous[0] != _json(option_ids):
                raise _invalid("Ask options conflict with original ask")
            return
        connection.execute(
            "INSERT INTO executor_asks "
            "(ask_key,reference_json,option_ids_json,executor_incarnation,"
            "child_id,conversation_id,connection_generation,reachability,observed_at_ms) "
            "VALUES (?,?,?,?,?,?,0,?,?)",
            (
                _ask_key(ask),
                _record(ask),
                _json(option_ids),
                ask.child.executor_incarnation,
                ask.child.child_id,
                ask.child.conversation_id,
                AskReachability.uncertain.value,
                now_ms,
            ),
        )

    def reconcile(self, inventory: ReconciliationInventory, now_ms: int) -> ReconciliationResult:
        receipts: list[AcceptanceReceipt] = []
        answers: list[DurableAnswer] = []
        evidence: list[AskEvidence] = []
        acknowledged_commands: list[str] = []
        with self._transaction() as connection:
            self._connected(connection, inventory.identity)
            for queued in inventory.queued_envelopes:
                row = connection.execute(
                    "SELECT * FROM executor_receipts WHERE sender_id=? AND submission_id=?",
                    (queued.sender_id, queued.submission_id),
                ).fetchone()
                if row:
                    if (
                        row["payload_sha256"] != queued.payload_sha256
                        or row["conversation_id"] != queued.conversation_id
                        or row["sequence"] != queued.sequence
                    ):
                        raise _invalid("Reconciliation submission conflicts with receipt")
                    receipts.append(self._receipt(row))
            for outcome in inventory.retained_command_outcomes:
                recorded = connection.execute(
                    "SELECT outcome_json FROM executor_command_outcomes "
                    "WHERE executor_incarnation=? AND command_id=?",
                    (outcome.child.executor_incarnation, outcome.command_id),
                ).fetchone()
                if recorded is not None:
                    if recorded[0] != _record(outcome):
                        raise _invalid("Command outcome conflicts with first result")
                    acknowledged_commands.append(outcome.command_id)
            for row in connection.execute("SELECT * FROM executor_asks").fetchall():
                ask = _parse(AskReference, row["reference_json"])
                reachability = callback_reachability(ask, inventory.children)
                connection.execute(
                    "UPDATE executor_asks SET connection_generation=?,reachability=?,"
                    "observed_at_ms=? "
                    "WHERE ask_key=?",
                    (
                        inventory.identity.connection_generation,
                        reachability.value,
                        now_ms,
                        row["ask_key"],
                    ),
                )
                evidence.append(
                    AskEvidence(
                        ask,
                        inventory.identity.connection_generation,
                        reachability,
                        now_ms,
                        row["answer_id"],
                        row["applied_at_mac_ms"],
                    )
                )
                if row["answer_id"] and row["applied_at_mac_ms"] is None:
                    # Only surviving exact callbacks receive stored answers. Lost answers
                    # remain durable, visible and unresolved; never target a replacement.
                    if reachability == AskReachability.reachable:
                        answers.append(self._answer(row))
        return ReconciliationResult(
            inventory.identity,
            tuple(receipts),
            tuple(answers),
            tuple(evidence),
            (),
        )

    @staticmethod
    def _answer(row: sqlite3.Row) -> DurableAnswer:
        return DurableAnswer(
            row["answer_id"],
            _parse(AskReference, row["reference_json"]),
            row["option_id"],
            row["accepted_at_vps_ms"],
        )

    def choose_answer(
        self, ask: AskReference, option_id: str, generation: int, now_ms: int
    ) -> DurableAnswer:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            if row is None:
                raise _invalid("Unknown permission ask")
            if row["answer_id"] is not None:
                if row["option_id"] != option_id:
                    raise _invalid("Permission choice is already recorded")
                return self._answer(row)
            current = connection.execute(
                "SELECT * FROM executor_connections WHERE active=1"
            ).fetchone()
            if (
                current is None
                or current["connection_state"] != ConnectionState.ready.value
                or current["executor_incarnation"] != ask.child.executor_incarnation
                or current["connection_generation"] != generation
                or row["connection_generation"] != generation
                or row["reachability"] != AskReachability.reachable.value
            ):
                raise _invalid("Current callback reachability is unconfirmed")
            if option_id not in json.loads(row["option_ids_json"]):
                raise _invalid("Unknown permission option")
            answer = DurableAnswer(str(uuid4()), ask, option_id, now_ms)
            connection.execute(
                "UPDATE executor_asks SET answer_id=?,option_id=?,accepted_at_vps_ms=? "
                "WHERE ask_key=?",
                (answer.answer_id, answer.option_id, now_ms, _ask_key(ask)),
            )
            return answer

    def record_application(
        self, application: AnswerApplication, *, identity: ExecutorIdentity
    ) -> None:
        with self._transaction() as connection:
            self._connected(connection, identity)
            self.record_application_in_transaction(connection, application)

    @staticmethod
    def record_application_in_transaction(
        connection: sqlite3.Connection, application: AnswerApplication
    ) -> None:
        row = connection.execute(
            "SELECT answer_id,applied_at_mac_ms FROM executor_asks WHERE ask_key=?",
            (_ask_key(application.ask),),
        ).fetchone()
        if row is None or row[0] != application.answer_id:
            raise _invalid("Application does not match the recorded answer")
        if row[1] is not None and row[1] != application.applied_at_mac_ms:
            raise _invalid("Application conflicts with the first acknowledgment")
        connection.execute(
            "UPDATE executor_asks SET applied_at_mac_ms=? WHERE ask_key=?",
            (application.applied_at_mac_ms, _ask_key(application.ask)),
        )

    def record_action(
        self, action: AuthorizedAction, sample: ClockSample | None, *, identity: ExecutorIdentity
    ) -> None:
        with self._transaction() as connection:
            self._connected(connection, identity)
            self.record_action_in_transaction(connection, action, sample)

    @staticmethod
    def record_action_in_transaction(
        connection: sqlite3.Connection, action: AuthorizedAction, sample: ClockSample | None
    ) -> None:
        row = connection.execute(
            "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(action.ask),)
        ).fetchone()
        if row is None or row["answer_id"] != action.answer_id:
            raise _invalid("Action does not match the recorded answer")
        if row["action_json"] is not None:
            if row["action_json"] != _record(action):
                raise _invalid("Action conflicts with first observed action")
            return
        connection.execute(
            "UPDATE executor_asks SET action_json=?,clock_sample_json=? WHERE ask_key=?",
            (_record(action), _record(sample) if sample else None, _ask_key(action.ask)),
        )

    def ask_evidence(self, ask: AskReference) -> AskEvidence | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            if row is None:
                return None
            return AskEvidence(
                ask,
                row["connection_generation"],
                AskReachability(row["reachability"]),
                row["observed_at_ms"],
                row["answer_id"],
                row["applied_at_mac_ms"],
            )
        finally:
            connection.close()

    def action_timing(self, ask: AskReference) -> AnswerActionTiming | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            if row is None or row["answer_id"] is None:
                return None
            return answer_action_timing(
                self._answer(row),
                _parse(AuthorizedAction, row["action_json"]) if row["action_json"] else None,
                _parse(ClockSample, row["clock_sample_json"]) if row["clock_sample_json"] else None,
            )
        finally:
            connection.close()

    @staticmethod
    def _publication_matches(connection: sqlite3.Connection, manifest: FileManifest) -> bool:
        row = connection.execute(
            "SELECT manifest_json,state FROM executor_publications WHERE publication_id=?",
            (manifest.publication_id,),
        ).fetchone()
        return row is not None and row[0] == _record(manifest) and row[1] == "accepted"

    def publication_accepted(self, manifest: FileManifest) -> bool:
        connection = connect(self.db_path)
        try:
            return self._publication_matches(connection, manifest)
        finally:
            connection.close()

    def begin_publication(self, manifest: FileManifest, *, identity: ExecutorIdentity) -> None:
        """Persist path reservation before rename; a crash cannot reorder versions."""
        with self._transaction() as connection:
            self._connected(connection, identity)
            previous = connection.execute(
                "SELECT manifest_json FROM executor_publications WHERE publication_id=?",
                (manifest.publication_id,),
            ).fetchone()
            if previous is not None:
                if previous[0] != _record(manifest):
                    raise _invalid("Publication identity conflicts with original manifest")
                return
            pending = connection.execute(
                "SELECT publications.state FROM executor_publication_paths paths "
                "JOIN executor_publications publications USING(publication_id) "
                "WHERE paths.scope=? AND paths.owner_id=? AND paths.relative_path=?",
                (manifest.scope.value, manifest.owner_id, manifest.relative_path),
            ).fetchone()
            if pending is not None and pending[0] != "accepted":
                raise _invalid("Earlier path publication requires recovery")
            connection.execute(
                "INSERT INTO executor_publications VALUES (?,?,?,?,NULL)",
                (manifest.publication_id, _record(manifest), manifest.sha256, "staged"),
            )
            connection.execute(
                "INSERT INTO executor_publication_paths VALUES (?,?,?,?) "
                "ON CONFLICT(scope,owner_id,relative_path) DO UPDATE SET "
                "publication_id=excluded.publication_id",
                (
                    manifest.scope.value,
                    manifest.owner_id,
                    manifest.relative_path,
                    manifest.publication_id,
                ),
            )

    def publication_manifest(self, publication_id: str) -> FileManifest | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT manifest_json FROM executor_publications WHERE publication_id=?",
                (publication_id,),
            ).fetchone()
            return _parse(FileManifest, row[0]) if row else None
        finally:
            connection.close()

    def accept_publication(
        self,
        manifest: FileManifest,
        publish: Callable[[], None],
        now_ms: int,
        *,
        identity: ExecutorIdentity,
    ) -> None:
        """Reserve durably, then serialize rename and acceptance against newer paths."""
        self.begin_publication(manifest, identity=identity)
        with self._transaction() as connection:
            self._connected(connection, identity)
            row = connection.execute(
                "SELECT state FROM executor_publications WHERE publication_id=?",
                (manifest.publication_id,),
            ).fetchone()
            if row is not None and row[0] == "accepted":
                return
            publish()
            connection.execute(
                "UPDATE executor_publications SET state='accepted',accepted_at_vps_ms=? "
                "WHERE publication_id=?",
                (now_ms, manifest.publication_id),
            )

    def evidence_for_conversation(self, conversation_id: str) -> tuple[AskEvidence, ...]:
        connection = connect(self.db_path)
        try:
            rows = connection.execute(
                "SELECT * FROM executor_asks WHERE conversation_id=?", (conversation_id,)
            ).fetchall()
            return tuple(
                AskEvidence(
                    _parse(AskReference, row["reference_json"]),
                    row["connection_generation"],
                    AskReachability(row["reachability"]),
                    row["observed_at_ms"],
                    row["answer_id"],
                    row["applied_at_mac_ms"],
                )
                for row in rows
            )
        finally:
            connection.close()

    @classmethod
    def observe_callback_in_transaction(
        cls,
        connection: sqlite3.Connection,
        ask: AskReference,
        identity: ExecutorIdentity,
        now_ms: int,
    ) -> None:
        cls._connected(connection, identity)
        if ask.child.executor_incarnation != identity.executor_incarnation:
            raise _invalid("Callback belongs to a different executor incarnation")
        changed = connection.execute(
            "UPDATE executor_asks SET connection_generation=?,reachability=?,observed_at_ms=? "
            "WHERE ask_key=?",
            (
                identity.connection_generation,
                AskReachability.reachable.value,
                now_ms,
                _ask_key(ask),
            ),
        ).rowcount
        if changed != 1:
            raise _invalid("Callback has no accepted ask row")

    def recorded_answer(self, ask: AskReference) -> DurableAnswer | None:
        connection = connect(self.db_path)
        try:
            row = connection.execute(
                "SELECT * FROM executor_asks WHERE ask_key=?", (_ask_key(ask),)
            ).fetchone()
            return self._answer(row) if row is not None and row["answer_id"] is not None else None
        finally:
            connection.close()

    @staticmethod
    def record_command_outcome_in_transaction(
        connection: sqlite3.Connection, outcome: CommandOutcome
    ) -> None:
        existing = connection.execute(
            "SELECT outcome_json FROM executor_command_outcomes "
            "WHERE executor_incarnation=? AND command_id=?",
            (outcome.child.executor_incarnation, outcome.command_id),
        ).fetchone()
        if existing is not None:
            if existing[0] != _record(outcome):
                raise _invalid("Command outcome conflicts with first result")
            return
        connection.execute(
            "INSERT INTO executor_command_outcomes VALUES (?,?,?)",
            (outcome.child.executor_incarnation, outcome.command_id, _record(outcome)),
        )

    def verify_connection(self, identity: ExecutorIdentity) -> None:
        with self._transaction() as connection:
            self._connected(connection, identity)

    def preflight(
        self, envelope: QueuedEnvelope, *, identity: ExecutorIdentity
    ) -> AcceptanceReceipt | None:
        """Validate prior result/order/files before a separately serialized native act.

        This grants no accepted receipt. The endpoint retains the per-conversation
        lock through native outcome recording and must preserve sender dedup IDs.
        """
        with self._transaction() as connection:
            return self._preflight(connection, envelope, identity)

    def _preflight(
        self,
        connection: sqlite3.Connection,
        envelope: QueuedEnvelope,
        identity: ExecutorIdentity,
    ) -> AcceptanceReceipt | None:
        # Import lazily: journal owns the shared canonical digest, not app acceptance.
        from planner.executor.journal import envelope_payload_digest

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
        self._connected(connection, identity)
        previous = connection.execute(
            "SELECT * FROM executor_receipts WHERE sender_id=? AND submission_id=?",
            (envelope.sender_id, envelope.submission_id),
        ).fetchone()
        if previous is not None:
            if (
                previous["payload_sha256"] != envelope.payload_sha256
                or previous["conversation_id"] != envelope.conversation_id
                or previous["sequence"] != envelope.sequence
            ):
                raise _invalid("Submission identity conflicts with its first receipt")
            return self._receipt(previous)
        latest = connection.execute(
            "SELECT COALESCE(MAX(sequence),0) FROM executor_receipts "
            "WHERE sender_id=? AND conversation_id=?",
            (envelope.sender_id, envelope.conversation_id),
        ).fetchone()[0]
        if envelope.sequence != latest + 1:
            raise _invalid("Earlier conversation submission has no receipt")
        for manifest in envelope.file_dependencies:
            if not self._publication_matches(connection, manifest):
                raise _invalid("File dependency is not durably published")
        return None

    @classmethod
    def revoke_turn_callbacks_in_transaction(
        cls,
        connection: sqlite3.Connection,
        child: ChildReference,
        turn: TurnToken,
        identity: ExecutorIdentity,
        now_ms: int,
    ) -> None:
        cls._connected(connection, identity)
        rows = connection.execute(
            "SELECT ask_key,reference_json FROM executor_asks "
            "WHERE executor_incarnation=? AND child_id=? AND conversation_id=?",
            (child.executor_incarnation, child.child_id, child.conversation_id),
        ).fetchall()
        for row in rows:
            ask = _parse(AskReference, row["reference_json"])
            if ask.turn == turn:
                connection.execute(
                    "UPDATE executor_asks SET reachability=?,observed_at_ms=? WHERE ask_key=?",
                    (AskReachability.callback_gone.value, now_ms, row["ask_key"]),
                )
