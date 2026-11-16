"""Request-scoped probes of the real reference runtime, with no adopting reads."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from typing import Any

from planner.conversation.api import delivery_fate_json
from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.message_content import text_message_content
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind

from .record_inspection import record_rows
from .scripted_registry import ScriptedBackendRegistry


class PendingSteerProbe:
    """Own one real steer and its ending observer until release or host shutdown."""

    def __init__(
        self, system: Callable[[], Any], registry: ScriptedBackendRegistry, db_path: str
    ) -> None:
        self._system = system
        self.registry = registry
        self.db_path = db_path
        self.pending: dict[str, Any] | None = None

    @property
    def system(self) -> Any:
        return self._system()

    def require(self, identity: str) -> dict[str, Any]:
        if self.pending is None or self.pending["id"] != identity:
            raise ValueError("No matching pending steer probe")
        return self.pending

    async def begin(self, identity: str, body: dict[str, Any]) -> dict[str, bool]:
        if self.pending is not None:
            raise ValueError("A pending steer probe is already occupied")
        child = self.registry.current[identity]
        steer = asyncio.create_task(
            self.system.send(
                identity,
                text_message_content(body["text"]),
                sender_label="owner",
                sender=OWNER_PRINCIPAL,
                recipient=Principal(
                    PrincipalKind(body["recipient"]["kind"]), body["recipient"]["id"]
                ),
                mode=PromptDeliveryMode.steer,
            )
        )
        self.pending = {
            "id": identity,
            "barrier": body["barrier"],
            "child": child,
            "steer": steer,
            "ending": None,
        }
        try:
            await child.wait_for_barrier(body["barrier"])
            return {"begun": True}
        except BaseException:
            steer.cancel()
            await asyncio.gather(steer, return_exceptions=True)
            self.pending = None
            raise

    def ending(self, identity: str, observer: Any) -> None:
        slot = self.require(identity)
        if slot["ending"] is not None:
            raise ValueError("The pending steer already owns an ending observer")
        slot["ending"] = asyncio.create_task(observer)

    def observe(self, identity: str) -> dict[str, Any]:
        slot = self.require(identity)
        rows = record_rows(self.db_path, identity)
        turn = self.system._conversations[identity].running_turn
        return {
            "events": [
                {key: row[key] for key in ("sequence", "kind", "payload")} for row in rows["events"]
            ],
            "owner_read_through_sequence": rows["conversation"]["owner_read_through_sequence"],
            "running": turn is not None,
            "ended": None if turn is None else turn.ended,
            "ending_done": slot["ending"] is not None and slot["ending"].done(),
        }

    async def release(self, identity: str) -> dict[str, Any]:
        slot = self.require(identity)
        try:
            await slot["child"].release(slot["barrier"])
            fate = await slot["steer"]
            if slot["ending"] is not None:
                await slot["ending"]
            await self.registry.current[identity].flush_events()
            await self.system.wait_until_quiescent()
            with closing(sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)) as connection:
                row = connection.execute(
                    "SELECT value FROM signal_publication_values WHERE conversation_id=?",
                    (identity,),
                ).fetchone()
                signal = None if row is None else json.loads(row[0])["attention"]["signal"]
            return {
                "fate": delivery_fate_json(fate),
                "ending_done": slot["ending"] is not None and slot["ending"].done(),
                "signal": signal,
            }
        finally:
            self.pending = None

    async def close(self) -> None:
        slot = self.pending
        if slot is None:
            return
        try:
            try:
                await slot["child"].release(slot["barrier"])
            except (RuntimeError, BrokenPipeError):
                pass
            await asyncio.gather(
                slot["steer"],
                *([slot["ending"]] if slot["ending"] is not None else []),
                return_exceptions=True,
            )
        finally:
            self.pending = None


async def live_status_before_adoption(
    system: Any, conversation_id: str, *,
    include_runtime_epoch: bool = False, db_path: str | None = None,
) -> dict[str, Any]:
    store = system._store
    before_keys = sorted(system._conversations)
    original = store.read_conversation
    reads = 0

    async def read(*args: Any, **kwargs: Any) -> Any:
        nonlocal reads
        reads += 1
        return await original(*args, **kwargs)

    store.read_conversation = read
    try:
        values = {}
        for name, identity in (
            ("retained", conversation_id),
            ("missing", f"{conversation_id}-missing"),
        ):
            values[name] = {
                "is_running": await system.is_running(identity),
                "has_pending_permission_ask": await system.has_pending_permission_ask(identity),
                "has_pending_user_input": await system.has_pending_user_input(identity),
            }
        result: dict[str, Any] = {
            "values": values,
            "read_count": reads,
            "before_keys": before_keys,
            "after_keys": sorted(system._conversations),
        }
        if include_runtime_epoch:
            if db_path is None:
                raise ValueError("Runtime epoch probe requires an admitted database path")
            with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                result["runtime_epoch_rows"] = [
                    dict(row) for row in connection.execute(
                        "SELECT singleton,epoch FROM conversation_runtime_epoch ORDER BY singleton"
                    )
                ]
        return result
    finally:
        store.read_conversation = original
