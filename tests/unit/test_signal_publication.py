"""Isolated producer proof with an injected transaction driver, not core compatibility."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Any, cast

import pytest

from planner.conversation.contracts import (
    SignalAttachment,
    SignalBatch,
    SignalParticipant,
    SignalSnapshot,
    SignalUnavailable,
)
from planner.conversation.publication import (
    PlainFieldMutation,
    SignalGuardLease,
    SignalPublicationCoordinator,
    SignalRuntimeMutation,
    persistence,
)


@dataclass(slots=True)
class _Turn:
    pending_permission_ask_ids: set[str] = field(default_factory=set)
    pending_user_input_questions: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _Runtime:
    running_turn: _Turn | None = None


@contextmanager
def _callback_guard(connection: sqlite3.Connection) -> Iterator[None]:
    """Injected fixture guard; does not claim retained nested dispatcher coverage."""

    def authorize(
        action: int,
        first: str | None,
        second: str | None,
        database: str | None,
        trigger: str | None,
    ) -> int:
        if action in (sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_SAVEPOINT):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    try:
        yield
    finally:
        connection.set_authorizer(None)


@contextmanager
def _bind_admission(source: sqlite3.Connection, token: object) -> Iterator[None]:
    connection = cast(_InjectedConnection, source)
    previous, connection.token = connection.token, token
    try:
        yield
    finally:
        connection.token = previous


class _InjectedConnection(sqlite3.Connection):
    """Only drives the producer protocol. It is not a managed core replacement."""

    coordinator: SignalPublicationCoordinator
    token: object | None = None
    lease: SignalGuardLease | None = None

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        if sql.startswith("BEGIN"):
            self.lease = self.coordinator.enter(self, self.token)
        return super().execute(sql, parameters)

    def commit(self) -> None:
        if self.lease is None:
            return super().commit()
        lease, self.lease = self.lease, None
        try:
            self.coordinator.finalize_signal(self)
            super().commit()
        except BaseException as error:
            super().rollback()
            lease.settle("rolled_back", error)
            raise
        else:
            lease.settle("committed", None)
        finally:
            lease.release()

    def rollback(self) -> None:
        super().rollback()
        if self.lease is not None:
            lease, self.lease = self.lease, None
            lease.settle("rolled_back", None)
            lease.release()

    def close(self) -> None:
        self.rollback()
        super().close()


class _Harness:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.runtime = _Runtime()
        self.runtimes = {"c": self.runtime}
        self.ownership = path.with_suffix(".ownership").open("w+b")
        raw = sqlite3.connect(path, isolation_level=None)
        raw.executescript("""
            CREATE TABLE conversations(conversation_id TEXT PRIMARY KEY,
                                       owner_read_through_sequence INTEGER DEFAULT 0);
            CREATE TABLE conversation_events(conversation_id TEXT,sequence INTEGER,
                                             kind TEXT,payload TEXT);
            CREATE TABLE conversation_error_acknowledgements(conversation_id TEXT,
                                                            through_sequence INTEGER);
            CREATE TABLE conversation_lifecycle_facts(conversation_id TEXT PRIMARY KEY);
            CREATE TABLE tickets(id TEXT PRIMARY KEY,conversation_id TEXT);
            CREATE TABLE ticket_conversations(ticket_id TEXT,conversation_id TEXT);
            CREATE TABLE agents(agent_key TEXT PRIMARY KEY,conversation_id TEXT);
            CREATE TABLE installed(cursor INTEGER);
            INSERT INTO conversations VALUES('c',0);
        """)
        raw.close()

        def authority(path: str, handle: IO[bytes]) -> None:
            if handle is not self.ownership or handle.closed:
                raise SignalUnavailable("fixture authority is lost")

        self.coordinator = SignalPublicationCoordinator(
            str(path),
            self.ownership,
            connect=self.connect,
            authority_check=authority,
            savepoint_depth=lambda conn: 0,
            transaction_control=_callback_guard,
            bind_admission=_bind_admission,
        )
        self.coordinator.bind_runtime(
            lambda identity: (
                self.runtimes[identity].running_turn if identity in self.runtimes else None
            )
        )
        with self.coordinator.bootstrap_authority(self.ownership) as token:
            connection = self.connect()
            connection.token = token
            connection.execute("BEGIN IMMEDIATE")
            persistence.create_schema(connection)
            persistence.install_source_triggers(connection)
            connection.commit()
            initialization = self.coordinator.begin_initialization(token)
            connection.token = initialization
            connection.execute("BEGIN IMMEDIATE")
            self.coordinator.initialize(connection)
            connection.commit()
            connection.close()
        self.batches: list[SignalBatch] = []

    def connect(self) -> _InjectedConnection:
        connection = sqlite3.connect(self.path, isolation_level=None, factory=_InjectedConnection)
        connection.coordinator = self.coordinator
        self.coordinator.attach_connection(connection)
        return connection

    def attach(self, participant: SignalParticipant | None = None) -> SignalAttachment:
        def install(connection: sqlite3.Connection, snapshot: SignalSnapshot) -> None:
            connection.execute("DELETE FROM installed")
            connection.execute("INSERT INTO installed VALUES(?)", (snapshot.cursor.sequence,))

        return self.coordinator.attach_signal_publication(
            install=install,
            participate=participant or (lambda conn, batch: self.batches.append(batch)),
            unavailable=lambda invalidation: None,
        )

    def mutation(
        self, value: _Turn | None, operation: str = "op", identity: str = "c"
    ) -> _InjectedConnection:
        connection = self.connect()
        token = self.coordinator.prepare_mutation(
            connection,
            SignalRuntimeMutation(
                identity,
                self.coordinator.runtime_epoch,
                self.runtimes[identity].running_turn,
                (PlainFieldMutation(self.runtimes[identity], "running_turn", value),),
            ),
            operation_id=operation,
            retain_failure=True,
        )
        connection.token = token
        connection.execute("BEGIN IMMEDIATE")
        self.coordinator.apply_mutation(connection, token)
        return connection

    def close(self) -> None:
        self.ownership.close()


@pytest.fixture
def producer(tmp_path: Path) -> Iterator[_Harness]:
    harness = _Harness(tmp_path / "signal.db")
    yield harness
    harness.close()


def test_complete_install_does_not_advance_cursor_and_runtime_mutation_is_reversible(
    producer: _Harness,
) -> None:
    attachment = producer.attach()
    permit = attachment.capture_permit()
    connection = producer.mutation(_Turn())
    with pytest.raises(SignalUnavailable, match="candidate"):
        attachment.validate(permit)
    connection.rollback()
    assert producer.runtime.running_turn is None
    connection.close()
    connection = producer.mutation(_Turn())
    connection.commit()
    assert len(producer.batches) == 1
    batch = producer.batches[0]
    assert batch.base_cursor.sequence == 0
    assert batch.cursor.sequence == 1
    assert batch.replacements[0].attention.signal["working"]
    assert persistence.operation_result(connection, "op") is not None
    connection.close()


def test_unrelated_transaction_is_not_a_publication_or_write(producer: _Harness) -> None:
    producer.attach()
    connection = producer.connect()
    connection.execute("BEGIN")
    before = connection.total_changes
    connection.execute("SELECT * FROM conversations").fetchall()
    connection.commit()
    assert connection.total_changes == before
    assert persistence.cursor(connection).sequence == 0
    assert producer.batches == []
    connection.close()


def test_owner_message_read_failure_and_pending_sets_remain_independent(producer: _Harness) -> None:
    producer.attach()
    connection = producer.mutation(_Turn({"permission"}, {"input": object()}))
    connection.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}')")
    connection.execute(
        "INSERT INTO conversation_events VALUES('c',2,'turn_ended','{\"ending\":\"failed\"}')"
    )
    connection.commit()
    attention = producer.batches[-1].replacements[0].attention
    assert attention.signal == {"working": True, "message_for_owner": True}
    assert attention.awaiting_answer and attention.last_turn_failed
    connection.close()
    connection = producer.connect()
    connection.execute("BEGIN")
    connection.execute("UPDATE conversations SET owner_read_through_sequence=1")
    connection.execute("INSERT INTO conversation_events VALUES('c',3,'message_to_owner','{}')")
    connection.commit()
    # Read through 1 cannot clear message 3; unchanged facts still order the batch.
    assert producer.batches[-1].replacements == ()
    connection.close()


def test_participant_rejection_undoes_runtime_and_sql_and_fences_before_release(
    producer: _Harness,
) -> None:
    def reject(connection: sqlite3.Connection, batch: SignalBatch) -> None:
        raise RuntimeError("reject candidate")

    attachment = producer.attach(reject)
    permit = attachment.capture_permit()
    connection = producer.mutation(_Turn())
    connection.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}')")
    with pytest.raises(RuntimeError, match="reject candidate"):
        connection.commit()
    assert producer.runtime.running_turn is None
    assert connection.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0] == 0
    assert persistence.cursor(connection).sequence == 0
    with pytest.raises(SignalUnavailable):
        attachment.validate(permit)
    connection.close()


def test_participants_cannot_change_source_private_state_or_control_transaction(
    producer: _Harness,
) -> None:
    attempts = []

    def participant(connection: sqlite3.Connection, batch: SignalBatch) -> None:
        for sql in (
            "UPDATE conversations SET owner_read_through_sequence=99",
            "UPDATE signal_publication_meta SET sequence=99",
            "COMMIT",
        ):
            with pytest.raises(sqlite3.DatabaseError):
                connection.execute(sql)
            attempts.append(sql)

    producer.attach(participant)
    connection = producer.mutation(_Turn())
    connection.commit()
    assert len(attempts) == 3
    assert persistence.cursor(connection).sequence == 1
    connection.close()


def test_association_only_batches_capture_old_and_new_and_deletion_is_fenced(
    producer: _Harness,
) -> None:
    producer.attach()
    connection = producer.connect()
    connection.execute("BEGIN")
    connection.execute("INSERT INTO tickets VALUES('worker','c')")
    connection.execute("INSERT INTO agents VALUES('sprint_item_supervisor_example','c')")
    connection.commit()
    batch = producer.batches[-1]
    assert batch.replacements == ()
    assert {(effect.owner_kind, effect.owner_id) for effect in batch.associations} == {
        ("ticket", "worker"),
        ("orchestrator", "example"),
    }
    connection.execute("BEGIN")
    connection.execute("DELETE FROM conversations")
    connection.commit()
    assert producer.batches[-1].deletions[0].conversation_id == "c"
    connection.execute("BEGIN")
    connection.execute("INSERT INTO conversations VALUES('c',0)")
    with pytest.raises(SignalUnavailable, match="recreated"):
        connection.commit()
    connection.close()


def test_commit_uncertainty_retains_undo_and_requires_exact_evidence(producer: _Harness) -> None:
    producer.attach()
    connection = producer.mutation(_Turn())
    lease, connection.lease = connection.lease, None
    assert lease is not None
    producer.coordinator.finalize_signal(connection)
    sqlite3.Connection.rollback(connection)
    lease.settle("uncertain", RuntimeError("lost outcome"))
    lease.release()
    connection.close()
    with pytest.raises(SignalUnavailable, match="repair"):
        producer.attach()
    raw = sqlite3.connect(producer.path, isolation_level=None)
    raw.execute("BEGIN")
    with pytest.raises(SignalUnavailable, match="uncertain"):
        producer.coordinator.resolve_operation(raw, "op")
    assert producer.coordinator.resolve_operation(raw, "op", rollback_established=True) is None
    assert producer.runtime.running_turn is None
    with pytest.raises(SignalUnavailable, match="resolved"):
        producer.coordinator.confirm_repaired(raw)
    raw.close()
    retry = producer.mutation(_Turn())
    retry.commit()
    producer.coordinator.accept_operation_result("op")
    retry.close()
    raw = sqlite3.connect(producer.path, isolation_level=None)
    raw.execute("BEGIN")
    producer.coordinator.confirm_repaired(raw)
    raw.close()


def test_install_registration_failure_stays_unavailable_after_known_commit(
    producer: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object) -> None:
        raise RuntimeError("registration failed")

    monkeypatch.setattr(producer.coordinator, "_register_callbacks", fail)
    with pytest.raises(RuntimeError, match="registration"):
        producer.attach()
    connection = producer.connect()
    assert connection.execute("SELECT cursor FROM installed").fetchone()[0] == 0
    assert producer.coordinator.signal_availability().state == "unavailable"
    connection.close()


def test_reentrant_detach_and_savepoint_after_runtime_mutation_are_rejected(
    producer: _Harness,
) -> None:
    attachment = producer.attach()
    connection = producer.mutation(_Turn())
    with pytest.raises(SignalUnavailable, match="candidate"):
        attachment.close()
    with pytest.raises(RuntimeError, match="savepoint"):
        assert connection.lease is not None
        connection.lease.check_savepoint("create")
    connection.rollback()
    connection.close()
    attachment.close()
    attachment.close()
    assert not producer.ownership.closed


def test_degraded_durable_knowledge_does_not_fabricate_runtime_idle(producer: _Harness) -> None:
    attachment = producer.attach()
    connection = producer.connect()
    connection.execute("BEGIN")
    connection.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}')")
    connection.commit()
    attachment.close()
    availability = producer.coordinator.signal_availability()
    raw = sqlite3.connect(producer.path, isolation_level=None)
    raw.execute("BEGIN")
    knowledge = producer.coordinator.read_durable_signal_knowledge(
        raw,
        ("c", "missing"),
        availability.generation,
    )
    assert knowledge[0].current is None
    assert knowledge[0].durable is not None
    assert knowledge[0].durable.message_for_owner
    assert knowledge[1].durable is None
    with pytest.raises(SignalUnavailable, match="generation"):
        producer.coordinator.read_durable_signal_knowledge(raw, ("c",), availability.generation - 1)
    raw.close()
    connection.close()


def test_late_runtime_start_after_deletion_fails_before_field_mutation(producer: _Harness) -> None:
    producer.attach()
    connection = producer.connect()
    connection.execute("BEGIN")
    connection.execute("DELETE FROM conversations")
    connection.commit()
    connection.close()
    with pytest.raises(SignalUnavailable, match="deleted"):
        producer.mutation(_Turn())
    # The helper leaves the failed candidate transaction to its owner.
    lease = next(iter(producer.coordinator._leases.values()))
    lease.connection.rollback()
    lease.connection.close()
    assert producer.runtime.running_turn is None


def test_replace_on_new_connection_captures_deleted_owner_and_rename_effects(
    producer: _Harness,
) -> None:
    producer.attach()
    connection = producer.connect()
    assert connection.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
    connection.execute("BEGIN")
    connection.execute("INSERT INTO tickets VALUES('worker','c')")
    connection.commit()
    connection.close()
    connection = producer.connect()
    connection.execute("BEGIN")
    connection.execute("INSERT OR REPLACE INTO tickets VALUES('worker',NULL)")
    connection.execute("INSERT INTO agents VALUES('sprint_item_supervisor_old','c')")
    connection.commit()
    effects = producer.batches[-1].associations
    assert any(
        effect.owner_id == "worker"
        and effect.before_conversation_id == "c"
        and effect.after_conversation_id is None
        for effect in effects
    )
    connection.execute("BEGIN")
    connection.execute("UPDATE agents SET agent_key='sprint_item_supervisor_new'")
    connection.commit()
    effects = producer.batches[-1].associations
    assert [
        (effect.owner_id, effect.before_conversation_id, effect.after_conversation_id)
        for effect in effects
    ] == [("old", "c", None), ("new", None, "c")]
    connection.close()


def test_commit_uncertainty_with_committed_evidence_never_undoes_or_repeats_append(
    producer: _Harness,
) -> None:
    producer.attach()
    turn = _Turn()
    connection = producer.mutation(turn)
    producer.coordinator.record_result(connection, {"sequence": 1})
    connection.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}')")
    lease, connection.lease = connection.lease, None
    assert lease is not None
    producer.coordinator.finalize_signal(connection)
    sqlite3.Connection.commit(connection)
    lease.settle("uncertain", RuntimeError("lost result"))
    lease.release()
    connection.close()
    raw = sqlite3.connect(producer.path, isolation_level=None)
    raw.execute("BEGIN")
    evidence = producer.coordinator.resolve_operation(raw, "op")
    assert evidence is not None
    assert evidence[1] == {"sequence": 1}
    assert producer.runtime.running_turn is turn
    assert raw.execute("SELECT COUNT(*) FROM conversation_events").fetchone()[0] == 1
    producer.coordinator.accept_operation_result("op")
    producer.coordinator.confirm_repaired(raw)
    raw.close()


def test_private_evidence_prunes_only_acknowledged_known_results(producer: _Harness) -> None:
    producer.attach()
    connection = producer.mutation(_Turn(), "first")
    connection.commit()
    producer.coordinator.accept_operation_result("first")
    connection.close()
    connection = producer.connect()
    connection.execute("BEGIN")
    before = connection.total_changes
    connection.commit()
    assert connection.total_changes == before
    assert persistence.operation_result(connection, "first") is not None
    connection.close()
    connection = producer.mutation(None, "second")
    connection.commit()
    assert persistence.operation_result(connection, "first") is None
    assert persistence.operation_result(connection, "second") is not None
    assert connection.execute("SELECT COUNT(*) FROM signal_publication_batches").fetchone()[0] == 1
    connection.close()


def test_gate_blocks_other_thread_from_observing_uncommitted_runtime(producer: _Harness) -> None:
    attachment = producer.attach()
    connection = producer.mutation(_Turn())
    started, finished = threading.Event(), threading.Event()
    observed = []

    def reader() -> None:
        started.set()
        with producer.coordinator.current_read():
            observed.append(producer.runtime.running_turn)
        finished.set()

    thread = threading.Thread(target=reader)
    thread.start()
    assert started.wait(2)
    assert not finished.wait(0.05)
    connection.rollback()
    assert finished.wait(2)
    thread.join()
    assert observed == [None]
    attachment.capture_permit()
    connection.close()


def test_initialization_does_not_admit_runtime_before_install_or_old_bootstrap_token(
    producer: _Harness,
) -> None:
    connection = producer.connect()
    with pytest.raises(SignalUnavailable, match="installation"):
        producer.coordinator.admit_runtime()
    with pytest.raises(SignalUnavailable, match="not open"):
        connection.execute("BEGIN")
    connection.close()
    producer.attach()
    producer.coordinator.admit_runtime()


def test_degraded_reader_rejects_other_database(producer: _Harness, tmp_path: Path) -> None:
    producer.attach().close()
    raw = sqlite3.connect(tmp_path / "other.db", isolation_level=None)
    raw.execute("BEGIN")
    with pytest.raises(SignalUnavailable, match="another Signal database"):
        producer.coordinator.read_durable_signal_knowledge(
            raw,
            ("c",),
            producer.coordinator.signal_availability().generation,
        )
    raw.close()


def test_complete_cross_conversation_snapshot_orders_competing_mutation_without_leakage(
    producer: _Harness,
) -> None:
    attachment = producer.attach()
    with producer.coordinator.runtime_registry():
        producer.runtimes["child"] = _Runtime()
    connection = producer.connect()
    connection.execute("BEGIN")
    connection.execute("INSERT INTO conversations VALUES('child',0)")
    connection.commit()
    connection.close()
    connection = producer.mutation(_Turn(), "child-start", "child")
    connection.commit()
    producer.coordinator.accept_operation_result("child-start")
    connection.close()
    attachment.close()
    started, done = threading.Event(), threading.Event()
    snapshots: list[SignalSnapshot] = []
    batches: list[SignalBatch] = []
    errors: list[BaseException] = []

    def mutate_parent() -> None:
        started.set()
        try:
            connection = producer.mutation(_Turn(), "parent-start")
            connection.commit()
            connection.close()
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    thread = threading.Thread(target=mutate_parent)

    def install(connection: sqlite3.Connection, snapshot: SignalSnapshot) -> None:
        snapshots.append(snapshot)
        thread.start()
        assert started.wait(2)
        assert not done.wait(0.05)

    producer.coordinator.attach_signal_publication(
        install=install,
        participate=lambda conn, batch: batches.append(batch),
        unavailable=lambda invalidation: None,
    )
    assert done.wait(2)
    thread.join()
    assert errors == []
    values = {value.conversation_id: value for value in snapshots[0].values}
    assert set(values) == {"c", "child"}
    assert values["c"].attention.signal["working"] is False
    assert values["child"].attention.signal["working"] is True
    assert batches[0].base_cursor == snapshots[0].cursor
    assert [value.conversation_id for value in batches[0].replacements] == ["c"]


def test_lifecycle_upsert_does_not_override_dirty_deduplication(producer: _Harness) -> None:
    producer.attach()
    connection = producer.connect()
    connection.execute("BEGIN")
    for _ in range(2):
        connection.execute(
            "INSERT INTO conversation_lifecycle_facts VALUES('c') "
            "ON CONFLICT(conversation_id) DO UPDATE SET conversation_id=excluded.conversation_id"
        )
    assert connection.execute("SELECT COUNT(*) FROM signal_publication_dirty").fetchone()[0] == 1
    connection.commit()
    assert len(producer.batches) == 1
    connection.close()


def test_reconciliation_handle_survives_release_and_rejects_foreign_stale_and_expired(
    producer: _Harness,
) -> None:
    producer.attach()
    connection = producer.mutation(_Turn())
    lease, connection.lease = connection.lease, None
    assert lease is not None
    handle = lease.reconciliation_handle
    producer.coordinator.finalize_signal(connection)
    sqlite3.Connection.commit(connection)
    lease.settle("uncertain", RuntimeError("lost result"))
    lease.release()
    assert lease.reconciliation_handle is handle
    connection.close()
    raw = sqlite3.connect(producer.path, isolation_level=None)
    raw.execute("BEGIN")
    for forged in (replace(handle, producer_id="foreign"), replace(handle, batch_id="unknown")):
        with pytest.raises(SignalUnavailable):
            producer.coordinator.resolve_reconciliation(raw, forged, rollback_established=True)
    evidence = producer.coordinator.resolve_reconciliation(raw, handle)
    assert evidence is not None
    assert evidence[0] == handle.batch_id
    producer.coordinator.accept_operation_result(handle.operation_id)
    with pytest.raises(SignalUnavailable, match="expired"):
        producer.coordinator.resolve_reconciliation(raw, handle, rollback_established=True)
    raw.close()


def test_shutdown_stops_new_admission_without_closing_borrowed_handle(producer: _Harness) -> None:
    producer.attach()
    producer.coordinator.invalidate("shutdown")
    connection = producer.connect()
    with pytest.raises(SignalUnavailable, match="shutdown"):
        connection.execute("BEGIN")
    with pytest.raises(SignalUnavailable, match="shutdown"):
        producer.coordinator.prepare_mutation(connection, None, operation_id="late")
    assert not producer.ownership.closed
    connection.close()


def test_new_epoch_retires_private_evidence_and_runtime_without_clearing_unread(
    producer: _Harness,
) -> None:
    producer.attach()
    connection = producer.mutation(_Turn())
    assert connection.lease is not None
    handle = connection.lease.reconciliation_handle
    connection.execute("INSERT INTO conversation_events VALUES('c',1,'message_to_owner','{}')")
    connection.commit()
    connection.close()
    old = producer.coordinator
    old.invalidate("shutdown")
    producer.runtime.running_turn = None
    producer.coordinator = SignalPublicationCoordinator(
        str(producer.path),
        producer.ownership,
        connect=producer.connect,
        authority_check=lambda path, ownership: None,
        transaction_control=_callback_guard,
        bind_admission=_bind_admission,
        savepoint_depth=lambda connection: 0,
    )
    producer.coordinator.bind_runtime(lambda identity: producer.runtime.running_turn)
    with producer.coordinator.bootstrap_authority(producer.ownership) as bootstrap:
        initialization = producer.coordinator.begin_initialization(bootstrap)
        connection = producer.connect()
        connection.token = initialization
        connection.execute("BEGIN")
        producer.coordinator.initialize(connection)
        connection.commit()
        values = persistence.values(connection)
        assert values[0].attention.signal == {"working": False, "message_for_owner": True}
        assert values[0].runtime_epoch != old.runtime_epoch
        assert (
            connection.execute("SELECT COUNT(*) FROM signal_publication_batches").fetchone()[0] == 0
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM signal_publication_operations").fetchone()[0]
            == 1
        )
        with pytest.raises(SignalUnavailable):
            producer.coordinator.resolve_reconciliation(
                connection, handle, rollback_established=True
            )
        connection.close()
