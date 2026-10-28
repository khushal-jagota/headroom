"""Shared semantic notices and independently fenced bounded view delivery."""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

from planner.conversation.contracts import SignalAvailability, SignalUnavailable
from planner.core import change_signal
from planner.core.sse import register_open_stream_closer
from planner.navigation.contracts import Continuity, DurableSignal
from planner.navigation.delivery import (
    DeliveryRuntime,
    _read_connection,
    _require_current_knowledge,
)
from planner.navigation.delivery_authority import DeliveryAccess, DeliveryUnavailable
from planner.navigation.delivery_contracts import CurrentAuthority, DegradedAuthority
from planner.navigation.proof import certify
from planner.navigation.views.configuration import DEFAULT_CONFIG, ViewConfig
from planner.navigation.views.contracts import (
    VIEW_PROJECTION_VERSION,
    ViewContent,
    ViewRequest,
    ViewSnapshot,
)
from planner.navigation.views.logic import compact, digest, section_key
from planner.navigation.views.ordering import ORDERING_GENERATION
from planner.navigation.views.read import continuity, identity, read_content, window_key


@dataclass(eq=False)
class Subscription:
    keys: set[tuple[str, str]]
    sections: set[str]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    review_context: bool = False
    reading: bool = True
    dirty: bool = False
    reset: bool = False
    coverage: Continuity | None = None


def _content_ids(content: ViewContent) -> tuple[str, ...]:
    return tuple(sorted(row["id"] for row in content["records"] if row["kind"] == "conversation"))


def _candidate(
    parent: DeliveryRuntime, value: ViewRequest, current: bool, config: ViewConfig
) -> tuple[str, ...]:
    with contextlib.closing(_read_connection(parent.path)) as conn:
        conn.execute("BEGIN")
        return _content_ids(read_content(conn, value, current=current, config=config)[1])


def _snapshot(
    parent: DeliveryRuntime,
    value: ViewRequest,
    availability: SignalAvailability,
    access: DeliveryAccess,
    fence: object,
    inventory: tuple[str, ...],
    config: ViewConfig,
) -> tuple[Continuity, ViewContent, Any]:
    with contextlib.closing(_read_connection(parent.path)) as conn:
        conn.execute("BEGIN")
        # FIRST source read occurs under the producer gate, before SQL establishes its snapshot.
        observations = parent._require_source().read_durable_signal_knowledge(
            conn, inventory, availability.generation
        )
        parent.admission.validate_snapshot(conn, access)
        coverage, content = read_content(
            conn, value, current=availability.state == "current", config=config
        )
        if inventory != _content_ids(content) or len(observations) != len(inventory):
            raise DeliveryUnavailable("scoped conversation inventory changed")
        facts = {item.conversation_id: item for item in observations}
        if set(facts) != set(inventory):
            raise DeliveryUnavailable("scoped durable coverage is incomplete")
        for row in content["records"]:
            if row["kind"] != "conversation":
                continue
            observation = facts[row["id"]]
            if observation.availability != availability or observation.durable is None:
                raise DeliveryUnavailable("scoped durable profile changed")
            durable: DurableSignal = {
                "message_for_owner": observation.durable.message_for_owner,
                "last_turn_failed": observation.durable.last_turn_failed,
            }
            if any(type(bit) is not bool for bit in durable.values()):
                raise DeliveryUnavailable("durable facts require exact booleans")
            if availability.state == "current":
                _require_current_knowledge(row["value"]["attention"], durable)
            else:
                row["value"]["attention"] = {"current": None, "durable": durable}
        proof = (
            certify(conn, fence) if fence is not None and availability.state == "current" else None
        )
        return coverage, content, proof


def _notice(
    parent: DeliveryRuntime, previous: Continuity | None
) -> tuple[Continuity, set[tuple[str, str]], set[str], bool, bool]:
    with contextlib.closing(_read_connection(parent.path)) as conn:
        conn.execute("BEGIN")
        coverage = continuity(conn)
        if previous is None or any(
            dict(coverage)[k] != dict(previous)[k]
            for k in ("scope", "schema_version", "semantics", "epoch")
        ):
            return coverage, set(), set(), True, True
        after = int(previous["revision"])
        current = int(coverage["revision"])
        if current < after:
            return coverage, set(), set(), True, True
        rows = conn.execute(
            "SELECT revision,footprint FROM navigation_view_changes WHERE "
            "revision>? ORDER BY revision",
            (after,),
        ).fetchall()
        if current != after and (
            not rows
            or rows[0][0] != after + 1
            or rows[-1][0] != current
            or len(rows) != current - after
        ):
            return coverage, set(), set(), True, True
        keys: set[tuple[str, str]] = set()
        sections: set[str] = set()
        aggregate = False
        for _, raw in rows:
            item = json.loads(raw)
            keys.update(tuple(key) for key in item["keys"])
            sections.update(item["sections"])
            aggregate |= item["globals"]
        return coverage, keys, sections, aggregate, False


class ScopedDeliveryRuntime:
    def __init__(self, parent: DeliveryRuntime, config: ViewConfig = DEFAULT_CONFIG) -> None:
        from planner.navigation.views.api import router

        self.parent = parent
        self.config = config
        self.router = router(self)
        self._subscriptions: set[Subscription] = set()
        self._task: asyncio.Task[None] | None = None
        self._wake = asyncio.Event()
        self._closed = False
        self._unsubscribe: Any = None
        self._coverage: Continuity | None = None

    async def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("scoped navigation delivery already started")
        loop = asyncio.get_running_loop()
        self._closed = False

        def wake() -> None:
            loop.call_soon_threadsafe(self._wake.set)

        self._unsubscribe = change_signal.subscribe(wake)
        # Establish the coordinator cursor before accepting subscribers. A first notice
        # is initialization, not a reason to discard a freshly admitted snapshot.
        try:
            self._coverage = (await asyncio.to_thread(_notice, self.parent, None))[0]
        except (DeliveryUnavailable, RuntimeError, ValueError, sqlite3.Error):
            self._coverage = None
        self._task = asyncio.create_task(self._coordinate(), name="navigation-view-coordinator")

    async def close(self) -> None:
        self._closed = True
        if self._unsubscribe:
            self._unsubscribe()
        self._wake.set()
        for subscription in self._subscriptions:
            subscription.reset = True
            subscription.event.set()
        if self._task:
            await self._task
            self._task = None

    async def _coordinate(self) -> None:
        while not self._closed:
            self._wake.clear()
            try:
                coverage, keys, sections, aggregate, reset = await asyncio.to_thread(
                    _notice, self.parent, self._coverage
                )
                if self._coverage != coverage:
                    self._coverage = coverage
                    for subscription in self._subscriptions:
                        subscription.coverage = coverage
                        subscription.reset |= reset
                        subscription.dirty |= (
                            aggregate
                            or (subscription.reading and bool(keys or sections))
                            or bool(subscription.keys & keys)
                            or bool(subscription.sections & sections)
                            or subscription.review_context
                            and any(kind == "address" for kind, _ in keys)
                        )
                        subscription.event.set()
            except (DeliveryUnavailable, RuntimeError, ValueError, sqlite3.Error):
                for subscription in self._subscriptions:
                    subscription.reset = True
                    subscription.event.set()
            try:
                await asyncio.wait_for(self._wake.wait(), self.parent.config.poll_seconds)
            except TimeoutError:
                pass

    async def capture(
        self,
    ) -> tuple[SignalAvailability, DeliveryAccess, CurrentAuthority | DegradedAuthority]:
        def read() -> tuple[
            SignalAvailability, DeliveryAccess, CurrentAuthority | DegradedAuthority
        ]:
            access: DeliveryAccess
            available = self.parent._availability()
            if available.state == "current":
                access, authority = self.parent.current_read()
                return available, access, authority
            access = self.parent.admission.capture_degraded(
                lambda: self.parent._validate_profile(available)
            )
            return (
                available,
                access,
                {
                    "delivery_session_id": self.parent.delivery_session_id,
                    "availability": {
                        "state": available.state,
                        "generation": str(available.generation),
                        "reason": available.reason,
                    },
                    "permit": None,
                },
            )

        return await asyncio.to_thread(read)

    async def snapshot(
        self,
        value: ViewRequest,
        fence: object = None,
        *,
        captured: tuple[SignalAvailability, DeliveryAccess, CurrentAuthority | DegradedAuthority]
        | None = None,
    ) -> tuple[ViewSnapshot, DeliveryAccess]:
        available, access, authority = captured or await self.capture()
        try:
            inventory = await asyncio.to_thread(
                _candidate, self.parent, value, available.state == "current", self.config
            )
            coverage, content, proof = await asyncio.to_thread(
                _snapshot, self.parent, value, available, access, fence, inventory, self.config
            )
            await asyncio.to_thread(self.parent.admission.validate, access)
        except (
            SignalUnavailable,
            RuntimeError,
            ValueError,
            KeyError,
            TypeError,
            sqlite3.Error,
        ) as exc:
            raise DeliveryUnavailable("coherent scoped navigation is unavailable") from exc
        result = {
            "event": "snapshot",
            "wire_version": 3,
            "projection_version": VIEW_PROJECTION_VERSION,
            "view_id": identity(value),
            "scope_generation": value["scope_generation"],
            "ordering_generation": ORDERING_GENERATION,
            "coverage": coverage,
            "content_version": await asyncio.to_thread(digest, content),
            "mode": "current" if available.state == "current" else "degraded",
            "authority": authority,
            "content": content,
            "proof": proof,
        }
        return cast(ViewSnapshot, result), access

    async def stream(
        self,
        value: ViewRequest,
        captured: tuple[SignalAvailability, DeliveryAccess, CurrentAuthority | DegradedAuthority],
    ) -> AsyncIterator[bytes]:
        subscription = Subscription(
            set(),
            {window_key(window) for window in value["windows"]},
            review_context=bool(
                value["review_conversation_ids"]
                or value["review_skipped_keys"]
                or value["review_priority_keys"]
            ),
        )
        if value["review_skipped_keys"] or value["review_priority_keys"]:
            subscription.sections.add(section_key("review", None))
        self._subscriptions.add(subscription)  # Before initial read, so a wake cannot be lost.
        closed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def close() -> None:
            loop.call_soon_threadsafe(closed.set)

        forget_close = register_open_stream_closer(close)
        forget_authority = self.parent.admission.watch(closed, captured[1])
        try:
            previous, _ = await self.snapshot(value, captured=captured)
            subscription.keys = {(row["kind"], row["id"]) for row in previous["content"]["records"]}
            subscription.reading = False
            yield await asyncio.to_thread(encode_frame, previous)
            if previous["mode"] != "current":
                return
            while not self._closed and not closed.is_set():
                if subscription.event.is_set():
                    subscription.event.clear()
                    target = subscription.coverage
                    reset, dirty = subscription.reset, subscription.dirty
                    subscription.reset = subscription.dirty = False
                    if reset:
                        return
                    if target and int(target["revision"]) > int(previous["coverage"]["revision"]):
                        await asyncio.to_thread(self.parent.admission.validate, captured[1])
                        if dirty:
                            subscription.reading = True
                            current, _ = await self.snapshot(value, captured=captured)
                            subscription.reading = False
                        else:
                            current = cast(ViewSnapshot, {**previous, "coverage": target})
                        if current["coverage"]["epoch"] != previous["coverage"]["epoch"]:
                            return
                        frame = difference(previous, current)
                        previous = current
                        subscription.keys = {
                            (row["kind"], row["id"]) for row in current["content"]["records"]
                        }
                        yield await asyncio.to_thread(encode_frame, frame)
                        continue
                waits = [
                    asyncio.create_task(subscription.event.wait()),
                    asyncio.create_task(closed.wait()),
                ]
                try:
                    done, _ = await asyncio.wait(
                        waits,
                        timeout=self.parent.config.heartbeat_seconds,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if not done:
                        yield b": heartbeat\n\n"
                finally:
                    for task in waits:
                        task.cancel()
                    await asyncio.gather(*waits, return_exceptions=True)
        finally:
            forget_authority()
            forget_close()
            self._subscriptions.discard(subscription)


def difference(previous: ViewSnapshot, current: ViewSnapshot) -> dict[str, Any]:
    common = {
        key: dict(current)[key]
        for key in (
            "wire_version",
            "projection_version",
            "view_id",
            "scope_generation",
            "ordering_generation",
            "coverage",
            "content_version",
            "authority",
        )
    }
    common["previous_coverage"] = previous["coverage"]
    if current["content_version"] == previous["content_version"]:
        return {**common, "event": "checkpoint"}
    old = {(row["kind"], row["id"]): row for row in previous["content"]["records"]}
    new = {(row["kind"], row["id"]): row for row in current["content"]["records"]}
    return {
        **common,
        "event": "patch",
        "previous_content_version": previous["content_version"],
        "upserts": [row for key, row in new.items() if old.get(key) != row],
        "evictions": [{"kind": kind, "id": identity} for kind, identity in old.keys() - new.keys()],
        "windows": current["content"]["windows"],
        "counts": current["content"]["counts"],
        "review_context": current["content"]["review_context"],
        "review_summary": current["content"]["review_summary"],
    }


def encode_frame(value: object) -> bytes:
    item = cast(dict[str, Any], value)
    return f"event: {item['event']}\ndata: {compact(value)}\n\n".encode()
