"""Durable, bounded navigation replay. Wakes accelerate polling but never replace it."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal, TypedDict, cast

from planner.conversation.contracts import (
    SignalAvailability,
    SignalPublicationSource,
    SignalReadPermit,
    SignalUnavailable,
)
from planner.core import change_signal
from planner.core.sse import register_open_stream_closer
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.contracts import (
    SCHEMA_VERSION,
    SUPPORTED_SEMANTICS,
    AttentionKnowledge,
    Continuity,
    DurableSignal,
    RevisionBatch,
    Snapshot,
    parse_revision,
)
from planner.navigation.data import snapshot
from planner.navigation.delivery_authority import (
    DeliveryAccess,
    DeliveryAdmission,
    DeliveryRead,
    DeliveryUnavailable,
)
from planner.navigation.delivery_contracts import (
    CurrentAuthority,
    Cursor,
    ReplayPage,
    ResetRequired,
    SnapshotResponse,
    batch_frame,
    decode_cursor,
    encode_cursor,
    reset_frame,
)
from planner.navigation.proof_contracts import ProofResult
from planner.navigation.retention import prune


def _read_connection(path: Path) -> sqlite3.Connection:
    # No schema opens, migrations, journal-mode changes, or live writable fallback.
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    conn.execute("PRAGMA query_only=ON")
    return conn


class StoredSnapshot(TypedDict):
    """SQL provenance only; this is not a public transport response."""

    cursor: str
    snapshot: Snapshot
    proof: ProofResult | None


def read_snapshot(
    path: Path,
    fence: object = None,
    admission: DeliveryAdmission | None = None,
    read: DeliveryRead | None = None,
) -> StoredSnapshot:
    from planner.navigation.proof import certify

    with contextlib.closing(_read_connection(path)) as conn:
        conn.execute("BEGIN")
        try:
            if admission is not None:
                admission.validate_snapshot(conn, read)
            value = snapshot(conn)
            proof = certify(conn, fence) if fence is not None else None
            return {
                "cursor": encode_cursor(value),
                "snapshot": value,
                "proof": proof,
            }
        finally:
            conn.rollback()


def read_page(
    path: Path,
    cursor: Cursor,
    config: DeliveryConfig,
    admission: DeliveryAdmission | None = None,
    read: DeliveryRead | None = None,
    authority: CurrentAuthority | None = None,
) -> ReplayPage:
    with contextlib.closing(_read_connection(path)) as conn:
        conn.execute("BEGIN")
        try:
            if admission is not None:
                admission.validate_snapshot(conn, read)
            scope, schema, epoch, current = conn.execute(
                "SELECT scope,schema_version,epoch,revision FROM navigation_meta"
            ).fetchone()
            continuity: Continuity = {
                "scope": scope,
                "schema_version": schema,
                "semantics": cast(Literal["attention-knowledge-v4"], SUPPORTED_SEMANTICS),
                "epoch": epoch,
                "revision": str(current),
            }
            # Validate EVERY continuity field before duplicate/future/expired decisions.
            if (
                any(
                    dict(cursor)[key] != dict(continuity)[key]
                    for key in ("scope", "schema_version", "semantics", "epoch")
                )
                or schema != SCHEMA_VERSION
            ):
                raise ResetRequired("incompatible_continuity")
            after = parse_revision(cursor["revision"])
            if after > current:
                raise ResetRequired("future_cursor")
            floor_row = conn.execute(
                "SELECT epoch,floor_revision FROM navigation_delivery_retention WHERE singleton=1"
            ).fetchone()
            floor = int(floor_row[1]) if floor_row and floor_row[0] == epoch else 0
            if after < floor:
                raise ResetRequired("expired_history")
            if after == current:
                return {"batches": [], "has_more": False}
            # Inspect size before fetching bodies, including individually oversized batches.
            entries = conn.execute(
                "SELECT revision,length(CAST(body AS BLOB)) FROM navigation_journal "
                "WHERE revision>? ORDER BY revision LIMIT ?",
                (after, config.page_batch_count),
            ).fetchall()
            batches: list[RevisionBatch] = []
            used = 0
            expected = after + 1
            for revision, body_bytes in entries:
                if revision != expected:
                    raise ResetRequired("journal_gap")
                if body_bytes > config.frame_bytes:
                    if batches:
                        break
                    raise ResetRequired("subscriber_overflow")
                row = conn.execute(
                    "SELECT body FROM navigation_journal WHERE revision=?", (revision,)
                ).fetchone()
                batch = cast(RevisionBatch, json.loads(row[0]))
                if any(
                    dict(batch)[key] != dict(continuity)[key]
                    for key in ("scope", "schema_version", "semantics", "epoch")
                ) or (batch["base_revision"], batch["revision"]) != (
                    str(revision - 1),
                    str(revision),
                ):
                    raise ResetRequired("journal_gap")
                # Pure SQL callers inspect provenance, without inventing wire authority.
                # Current delivery always supplies its exact framed profile.
                frame_bytes = (
                    len(batch_frame(batch, authority)) if authority is not None else body_bytes
                )
                if frame_bytes > config.frame_bytes:
                    if batches:
                        break
                    raise ResetRequired("subscriber_overflow")
                if used + frame_bytes > config.page_bytes:
                    break
                batches.append(batch)
                used += frame_bytes
                expected += 1
            if not batches:
                raise ResetRequired("journal_gap")
            return {"batches": batches, "has_more": expected <= current}
        finally:
            conn.rollback()


def _inventory(path: Path) -> tuple[str, ...]:
    try:
        with contextlib.closing(_read_connection(path)) as conn:
            return tuple(
                str(row[0])
                for row in conn.execute(
                    "SELECT conversation_id FROM conversations ORDER BY conversation_id"
                )
            )
    except sqlite3.Error as exc:
        raise DeliveryUnavailable("conversation inventory is unavailable") from exc


def _require_current_knowledge(value: AttentionKnowledge, durable: DurableSignal) -> None:
    current = value["current"]
    if (
        set(value) != {"current", "durable"}
        or current is None
        or set(current) != {"signal", "awaiting_answer", "last_turn_failed"}
        or set(current["signal"]) != {"working", "message_for_owner"}
        or set(value["durable"]) != {"message_for_owner", "last_turn_failed"}
        or any(
            type(bit) is not bool
            for bit in (
                current["signal"]["working"],
                current["signal"]["message_for_owner"],
                current["awaiting_answer"],
                current["last_turn_failed"],
                value["durable"]["message_for_owner"],
                value["durable"]["last_turn_failed"],
            )
        )
        or value["durable"] != durable
        or current["signal"]["message_for_owner"] != durable["message_for_owner"]
        or current["last_turn_failed"] != durable["last_turn_failed"]
    ):
        raise DeliveryUnavailable("current publication disagrees with canonical durable knowledge")


def _knowledge_snapshot(
    path: Path,
    source: SignalPublicationSource,
    inventory: tuple[str, ...],
    availability: SignalAvailability,
    admission: DeliveryAdmission,
    read: DeliveryAccess,
    fence: object,
) -> StoredSnapshot:
    from planner.navigation.proof import certify

    try:
        with contextlib.closing(_read_connection(path)) as conn:
            conn.execute("BEGIN")
            try:
                # FIRST canonical source read: the domain acquires its gate before
                # establishing the SQL snapshot. Identity PRAGMAs do not pin it.
                knowledge = source.read_durable_signal_knowledge(
                    conn, inventory, availability.generation
                )
                actual = tuple(
                    str(row[0])
                    for row in conn.execute(
                        "SELECT conversation_id FROM conversations ORDER BY conversation_id"
                    )
                )
                if actual != inventory or len(knowledge) != len(actual):
                    raise DeliveryUnavailable(
                        "conversation inventory changed during knowledge read"
                    )
                by_id = {value.conversation_id: value for value in knowledge}
                if set(by_id) != set(actual):
                    raise DeliveryUnavailable("Signal durable knowledge coverage is incomplete")
                for observation in knowledge:
                    if observation.availability != availability or observation.durable is None:
                        raise DeliveryUnavailable("Signal durable knowledge profile is incomplete")
                    if any(
                        type(bit) is not bool
                        for bit in (
                            observation.durable.message_for_owner,
                            observation.durable.last_turn_failed,
                        )
                    ):
                        raise DeliveryUnavailable("Signal durable facts must be exact booleans")
                admission.validate_snapshot(conn, read)
                value = snapshot(conn)
                if (
                    value["schema_version"] != SCHEMA_VERSION
                    or value["semantics"] != SUPPORTED_SEMANTICS
                ):
                    raise DeliveryUnavailable("navigation knowledge schema is not activated")
                covered: set[str] = set()
                for record in value["records"]:
                    if record["kind"] != "conversation":
                        continue
                    identity = record["id"]
                    if identity in covered or identity not in by_id:
                        raise DeliveryUnavailable("stored conversation knowledge coverage changed")
                    covered.add(identity)
                    facts = by_id[identity].durable
                    if facts is None:
                        raise DeliveryUnavailable("extant conversation has no durable facts")
                    durable: DurableSignal = {
                        "message_for_owner": facts.message_for_owner,
                        "last_turn_failed": facts.last_turn_failed,
                    }
                    if availability.state == "current":
                        _require_current_knowledge(record["value"]["attention"], durable)
                    else:
                        record["value"]["attention"] = {"current": None, "durable": durable}
                if covered != set(actual):
                    raise DeliveryUnavailable("stored navigation omits an extant conversation")
                proof = certify(conn, fence) if fence is not None else None
                return {"cursor": encode_cursor(value), "snapshot": value, "proof": proof}
            finally:
                conn.rollback()
    except DeliveryUnavailable:
        raise
    except (sqlite3.Error, SignalUnavailable, ValueError, KeyError, TypeError, RuntimeError) as exc:
        raise DeliveryUnavailable("coherent navigation knowledge is unavailable") from exc


class DeliveryRuntime:
    def __init__(self, path: str | Path, config: DeliveryConfig | None = None) -> None:
        from planner.navigation.api import delivery_router

        self.path = Path(path)
        self.config = config or DeliveryConfig()
        self.ready = False
        self.admission = DeliveryAdmission()
        self.delivery_session_id = uuid.uuid4().hex
        self._source: SignalPublicationSource | None = None
        self._closing = asyncio.Event()
        self._retention_task: asyncio.Task[None] | None = None
        self._stream_closers: dict[asyncio.Event, asyncio.Event] = {}
        self.router = delivery_router(self)

    def bind_signal_source(self, source: SignalPublicationSource) -> None:
        if self._source is not None and self._source is not source:
            raise RuntimeError("a replaced Signal source requires a new delivery runtime")
        self._source = source

    def _require_source(self) -> SignalPublicationSource:
        if self._source is None:
            raise DeliveryUnavailable("navigation has no Signal knowledge source")
        return self._source

    def _availability(self) -> SignalAvailability:
        value = self._require_source().signal_availability()
        if (
            type(value.generation) is not int
            or value.generation < 0
            or value.state not in ("initializing", "current", "unavailable")
            or (value.reason is not None and not isinstance(value.reason, str))
            or (value.state == "current" and value.reason is not None)
        ):
            raise DeliveryUnavailable("invalid Signal availability profile")
        return value

    def _validate_profile(self, expected: SignalAvailability) -> None:
        if self._availability() != expected:
            raise DeliveryUnavailable("Signal availability profile changed")

    def current_read(self) -> tuple[DeliveryRead, CurrentAuthority]:
        availability = self._availability()
        if availability.state != "current":
            raise DeliveryUnavailable("Signal runtime knowledge is not current")
        read = self.admission.capture(lambda: self._validate_profile(availability))
        if read is None or not isinstance(read.permit, SignalReadPermit):
            raise DeliveryUnavailable("Signal current delivery requires an exact permit")
        return read, self._current_authority(read, availability)

    def _current_authority(
        self, read: DeliveryRead, availability: SignalAvailability
    ) -> CurrentAuthority:
        if not isinstance(read.permit, SignalReadPermit):
            raise DeliveryUnavailable("Signal current delivery requires an exact permit")
        permit = read.permit
        if (
            availability.state != "current"
            or type(permit.generation) is not int
            or permit.generation != availability.generation
            or not all(
                isinstance(identity, str) and identity
                for identity in (permit.scope, permit.runtime_epoch, permit.installation_id)
            )
        ):
            raise DeliveryUnavailable("Signal permit does not match availability")
        authority: CurrentAuthority = {
            "delivery_session_id": self.delivery_session_id,
            "availability": {
                "state": "current",
                "generation": str(availability.generation),
                "reason": None,
            },
            "permit": {
                "scope": permit.scope,
                "runtime_epoch": permit.runtime_epoch,
                "installation_id": permit.installation_id,
                "generation": str(permit.generation),
            },
        }
        return authority

    async def start(self) -> None:
        if self.ready:
            raise RuntimeError("navigation delivery already started")
        # Owner startup has already reconciled and committed a fresh epoch.
        await asyncio.to_thread(self._prune)
        self._closing.clear()
        self.ready = True
        self._retention_task = asyncio.create_task(self._retain(), name="navigation-retention")

    def _prune(self) -> None:
        from planner.core.db import commit_without_change_signal, connect

        with contextlib.closing(connect(str(self.path))) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                prune(conn, self.config)
                commit_without_change_signal(conn)
            except BaseException:
                conn.rollback()
                raise

    async def _retain(self) -> None:
        while not self._closing.is_set():
            try:
                await asyncio.wait_for(self._closing.wait(), self.config.retention_seconds)
            except TimeoutError:
                try:
                    await asyncio.to_thread(self._prune)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "navigation retention failed; retry next interval"
                    )

    async def close(self) -> None:
        self.ready = False
        self.admission.invalidate()
        self._closing.set()
        for closer in tuple(self._stream_closers):
            closer.set()
        drained = tuple(self._stream_closers.values())
        if drained:
            await asyncio.gather(*(done.wait() for done in drained))
        if self._retention_task is not None:
            await self._retention_task
            self._retention_task = None

    async def current_snapshot(
        self, fence: object = None
    ) -> tuple[SnapshotResponse, DeliveryAccess]:
        source = self._require_source()
        inventory = await asyncio.to_thread(_inventory, self.path)
        availability = self._availability()
        read: DeliveryAccess
        if availability.state == "current":
            read, authority = self.current_read()
            if self._availability() != availability:
                raise DeliveryUnavailable("Signal availability changed before SQL")
            value = await asyncio.to_thread(
                _knowledge_snapshot,
                self.path,
                source,
                inventory,
                availability,
                self.admission,
                read,
                fence,
            )
            result: SnapshotResponse = {
                "wire_version": 2,
                "mode": "current",
                "authority": authority,
                "cursor": value["cursor"],
                "snapshot": value["snapshot"],
                "proof": value["proof"],
            }
        else:
            read = self.admission.capture_degraded(lambda: self._validate_profile(availability))
            value = await asyncio.to_thread(
                _knowledge_snapshot,
                self.path,
                source,
                inventory,
                availability,
                self.admission,
                read,
                None,
            )
            result = {
                "wire_version": 2,
                "mode": "degraded",
                "authority": {
                    "delivery_session_id": self.delivery_session_id,
                    "availability": {
                        "state": availability.state,
                        "generation": str(availability.generation),
                        "reason": availability.reason,
                    },
                    "permit": None,
                },
                "cursor": None,
                "snapshot": value["snapshot"],
                "proof": None,
            }
        self.admission.validate(read)
        return result, read

    async def stream(
        self,
        encoded_cursor: str,
        read: DeliveryRead | None = None,
        authority: CurrentAuthority | None = None,
    ) -> AsyncIterator[bytes]:
        if read is None:
            read, authority = self.current_read()
        if authority is None:
            raise DeliveryUnavailable("navigation stream requires current authority")
        self.admission.validate(read)
        if authority != self._current_authority(read, self._availability()):
            raise DeliveryUnavailable("navigation stream authority profile changed")
        try:
            cursor = decode_cursor(encoded_cursor, self.config.cursor_bytes)
        except ResetRequired as exc:
            yield reset_frame(exc.reason)
            return
        loop = asyncio.get_running_loop()
        wake = asyncio.Event()
        closed = asyncio.Event()
        drained = asyncio.Event()
        forget_invalidation = self.admission.watch(closed, read)
        self._stream_closers[closed] = drained

        def hint() -> None:
            loop.call_soon_threadsafe(wake.set)

        def close() -> None:
            loop.call_soon_threadsafe(closed.set)

        unsubscribe = change_signal.subscribe(hint)
        forget = register_open_stream_closer(close)
        last_frame = time.monotonic()
        try:
            while self.ready and not closed.is_set():
                wake.clear()
                try:
                    page = await asyncio.to_thread(
                        read_page, self.path, cursor, self.config, self.admission, read, authority
                    )
                    self.admission.validate(read)
                except ResetRequired as exc:
                    self.admission.validate(read)
                    yield reset_frame(exc.reason)
                    return
                if closed.is_set() or not self.ready:
                    return
                for batch in page["batches"]:
                    # Each yield owns one complete revision; there is no per-record progress.
                    self.admission.validate(read)
                    yield batch_frame(batch, authority)
                    cursor = decode_cursor(encode_cursor(cast(Continuity, batch)))
                    last_frame = time.monotonic()
                    if closed.is_set() or not self.ready:
                        return
                if page["has_more"]:
                    continue
                if time.monotonic() - last_frame >= self.config.heartbeat_seconds:
                    self.admission.validate(read)
                    yield b": keep-alive\n\n"
                    last_frame = time.monotonic()
                waiter = asyncio.create_task(wake.wait())
                closer = asyncio.create_task(closed.wait())
                try:
                    await asyncio.wait(
                        (waiter, closer),
                        timeout=self.config.poll_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    waiter.cancel()
                    closer.cancel()
                    await asyncio.gather(waiter, closer, return_exceptions=True)
        except DeliveryUnavailable:
            return
        finally:
            self._stream_closers.pop(closed, None)
            forget_invalidation()
            forget()
            unsubscribe()
            drained.set()


def create_delivery_runtime(
    path: str | Path,
    config: DeliveryConfig | None = None,
) -> DeliveryRuntime:
    return DeliveryRuntime(path, config)
