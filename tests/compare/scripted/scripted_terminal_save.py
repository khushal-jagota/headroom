"""Selected terminal candidates execute inside the real transaction before rollback."""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from collections.abc import Callable
from typing import Any

from .record_inspection import record_rows
from .scripted_child import decode_scripted_event
from .scripted_registry import ScriptedBackendRegistry


class _RetryLog(logging.Handler):
    def __init__(
        self, identity: str, retries: list[dict[str, Any]], failed: Callable[[], None]
    ) -> None:
        super().__init__()
        self.identity = identity
        self.retries = retries
        self.failed = failed

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not message.startswith(f"conversation {self.identity} save failed "):
            return
        match = re.search(r"\(failure (\d+)\); retrying in ([0-9.]+) s", message)
        if match is not None:
            self.retries.append(
                {
                    "failure": int(match[1]),
                    "delay": float(match[2]),
                    "error": str(record.exc_info[1]) if record.exc_info else "",
                }
            )
            self.failed()


class TerminalSaveProbe:
    def __init__(
        self, system: Callable[[], Any], registry: ScriptedBackendRegistry, db_path: str
    ) -> None:
        self._system = system
        self.registry = registry
        self.db_path = db_path
        self.slot: dict[str, Any] | None = None

    def require(self, identity: str) -> dict[str, Any]:
        if self.slot is None or self.slot["id"] != identity:
            raise ValueError("No matching terminal save probe")
        return self.slot

    def arm(self, identity: str) -> dict[str, bool]:
        if self.slot is not None:
            raise ValueError("A terminal save probe is already occupied")
        store = self._system()._store
        original = store.commit_backend_event
        slot: dict[str, Any] = {
            "id": identity,
            "original": original,
            "failures": 0,
            "candidates": [],
            "retries": [],
            "first_failure": asyncio.Event(),
            "observer": None,
        }

        async def commit(writer: Any, *args: Any, **kwargs: Any) -> Any:
            def candidate(connection: sqlite3.Connection) -> Any:
                result, written = writer(connection)
                if any(
                    row.conversation_id == identity and row.kind == "turn_ended" for row in written
                ):

                    def count(table: str) -> int:
                        return int(
                            connection.execute(
                                f"SELECT count(*) FROM {table} WHERE conversation_id=?", (identity,)
                            ).fetchone()[0]
                        )

                    slot["candidates"].append(
                        {
                            "attempt": slot["failures"] + 1,
                            "events": count("conversation_events"),
                            "views": count("conversation_view_rows"),
                            "lifecycle": count("conversation_lifecycle_facts"),
                        }
                    )
                    if slot["failures"] < 8:
                        slot["failures"] += 1
                        raise sqlite3.OperationalError("disk unavailable")
                return result, written

            return await original(candidate, *args, **kwargs)

        handler = _RetryLog(identity, slot["retries"], slot["first_failure"].set)
        slot["handler"] = handler
        logging.getLogger("planner.conversation").addHandler(handler)
        store.commit_backend_event = commit
        self.slot = slot
        return {"armed": True}

    async def begin(self, identity: str) -> dict[str, Any]:
        slot = self.require(identity)
        if slot["observer"] is not None:
            raise ValueError("The terminal save probe has already begun")
        child = self.registry.current[identity]
        token = child.token
        if token is None:
            raise ValueError("A scripted turn must exist before terminal save")
        await child.emit(
            decode_scripted_event(
                {
                    "kind": "TurnEnded",
                    "values": {
                        "ending": "failed",
                        "error_summary": "the model refused",
                        "standard_error_tail": None,
                    },
                }
            ),
            turn=token,
        )
        slot["observer"] = asyncio.create_task(child.flush_events())
        await slot["first_failure"].wait()
        await child.emit(
            decode_scripted_event(
                {
                    "kind": "ToolCallStarted",
                    "values": {
                        "tool_call_id": "late",
                        "title": "Read a file",
                        "tool_kind": "read",
                    },
                }
            ),
            turn=token,
        )
        turn = self._system()._conversations[identity].running_turn
        return {
            "failures": slot["failures"],
            "events": [
                {key: row[key] for key in ("sequence", "kind")}
                for row in record_rows(self.db_path, identity)["events"]
            ],
            "running": turn is not None,
            "ended": None if turn is None else turn.ended,
        }

    async def join(self, identity: str) -> dict[str, Any]:
        slot = self.require(identity)
        try:
            if slot["observer"] is None:
                raise ValueError("The terminal save probe has not begun")
            await slot["observer"]
            await self.registry.current[identity].flush_events()
            await self._system().wait_until_quiescent()
            return {key: slot[key] for key in ("failures", "candidates", "retries")}
        finally:
            self._restore()

    def _restore(self) -> None:
        if self.slot is None:
            return
        self._system()._store.commit_backend_event = self.slot["original"]
        logging.getLogger("planner.conversation").removeHandler(self.slot["handler"])
        self.slot = None

    async def close(self) -> None:
        observer = None if self.slot is None else self.slot["observer"]
        self._restore()
        if observer is not None:
            await asyncio.gather(observer, return_exceptions=True)
