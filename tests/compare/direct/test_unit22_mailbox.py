"""One source-fresh SQLite exchange proves mailbox custody and owned async waits.

The codec proof covers no durable transition, signal, recovery read, or shutdown.
This exchange uses real source mailboxes and real database writers, without a
route host, executor, backend provider, or simulated asynchronous SQL engine.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sqlite3
import time
from contextlib import closing, suppress
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from pydantic import ValidationError
from tests.support.executor_presence import lease_request, mac_identity

from planner.conversation.contracts import PromptDeliveryMode
from planner.conversation.events import (
    PromptDeliveryUncertainEventPayload,
    PromptEventPayload,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.lifecycle_storage import apply_facts
from planner.conversation.message_content import text_message_content
from planner.core import change_signal
from planner.core.db import connect, create_schema
from planner.environments.update_attempts import UpdateConflict
from planner.executor import protocol as wire
from planner.executor.contracts import CommandSettlementReason
from planner.executor.store import ExecutorStore
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence import MacPresenceService, no_update_check
from planner.executor.vps_presence_store import accept_lease_in_transaction, read_presence

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_760_000_000_000
TURN = 3
BIG = 9007199254740993
ARCHIVED = 50_000
BASE = ARCHIVED + 6
OLD = "11111111-1111-4111-8111-111111111111"
NEW = "22222222-2222-4222-8222-222222222222"
HISTORICAL = "33333333-3333-4333-8333-333333333333"
IDENTITY = wire.encode_value(mac_identity(OLD))
SUCCESSOR = wire.encode_value(mac_identity(NEW))
CONVERSATIONS = (
    "basic",
    "expiry",
    "budget",
    "stop",
    "failed",
    "recover",
    "cancelled",
    "race",
    "pending",
)
ROWS = (
    "SELECT CAST(command_sequence AS TEXT) AS sequence,command_id,executor_incarnation,operation,"
    "conversation_id,child_id,CAST(turn_number AS TEXT) AS turn,command_json,state,created_at_ms,"
    "delivered_at_ms,finished_at_ms,outcome_json,typeof(command_sequence) AS sequence_type,"
    "typeof(turn_number) AS turn_type,typeof(created_at_ms) AS created_type,"
    "typeof(delivered_at_ms) AS delivered_type,typeof(finished_at_ms) AS finished_type "
    "FROM executor_commands WHERE command_id NOT LIKE 'archive-%' ORDER BY command_sequence"
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _outcome_json(value: object) -> str:
    return json.dumps(
        wire.as_object(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _typed(name: str, value: Any) -> Any:
    return wire.type_adapter(getattr(wire, name)).validate_json(_json(value))


def _child(conversation: str, incarnation: str = OLD, suffix: str = "") -> dict[str, Any]:
    return {
        "executor_incarnation": incarnation,
        "child_id": conversation + suffix,
        "conversation_id": conversation,
    }


def _command(
    name: str, operation: str = "write_prompt", conversation: str = "basic", **changes: Any
) -> dict[str, Any]:
    text = [{"text": "work é"}]
    arguments: dict[str, dict[str, Any]] = {
        "write_prompt": {
            "content": text,
            "sender_content": text,
            "sender_label": "owner",
            "mode": "queue",
        },
        "steer": {"content": text, "sender_label": "owner"},
        "answer_permission_ask": {"ask_id": "ask", "option_id": "allow"},
        "answer_user_input": {"request_id": "question", "answers": []},
        "cancel_running_turn": {},
        "stop": {},
        "start": {
            "resolved_start": {
                "conversation_id": conversation,
                "backend_key": "codex",
                "model": "fixture",
                "reasoning_effort": None,
                "access": "full",
                "workspace_folder": "/tmp/unit22",
                "role_materials": {"role_text": "Worker", "identity_environment_variables": []},
            },
            "canonical_skill_snapshot": {"sources": [], "required_skill_names": []},
        },
    }
    return {
        "command_id": name,
        "child": _child(conversation),
        "operation": operation,
        "turn": {"conversation_id": conversation, "turn_number": TURN},
        "arguments": arguments[operation],
        **changes,
    }


def _result(
    command: dict[str, Any], disposition: str = "succeeded", **result: Any
) -> dict[str, Any]:
    return {
        "command_id": command["command_id"],
        "child_outcome": {
            "command_id": command["command_id"],
            "child": command["child"],
            "disposition": disposition,
            "result": result,
        },
    }


def _statement(sql: str, *values: Any) -> dict[str, Any]:
    return {"sql": sql, "values": list(values)}


def _observe(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        queries = {
            "rows": ROWS,
            "runs": (
                "SELECT conversation_id,executor_incarnation,child_id,"
                "CAST(turn_number AS TEXT) AS turn,delivery_uncertain,stop_state,"
                "typeof(turn_number) AS turn_type,typeof(delivery_uncertain) AS uncertain_type "
                "FROM mac_runs ORDER BY conversation_id"
            ),
            "assignments": (
                "SELECT *,typeof(created_at_ms) AS created_type,"
                "typeof(closed_at_ms) AS closed_type "
                "FROM executor_child_assignments ORDER BY executor_incarnation,child_id"
            ),
            "presence": (
                "SELECT *,typeof(singleton) AS singleton_type,"
                "typeof(last_contact_at_ms) AS contact_type,typeof(last_lease_at_ms) AS lease_type,"
                "typeof(continuous_since_ms) AS continuous_type,"
                "typeof(going_to_sleep_at_ms) AS sleep_type FROM mac_executor_presence"
            ),
            "incarnations": (
                "SELECT *,typeof(first_leased_at_ms) AS first_type,"
                "typeof(replaced_at_ms) AS replaced_type "
                "FROM executor_incarnations ORDER BY executor_incarnation"
            ),
            "events": (
                "SELECT sequence,typeof(sequence) AS sequence_type,created_at,"
                "typeof(created_at) AS created_type FROM conversation_events "
                "WHERE conversation_id='recover' ORDER BY sequence"
            ),
            "counts": (
                "SELECT state,count(*) AS count FROM executor_commands "
                "GROUP BY state ORDER BY state"
            ),
            "total": (
                "SELECT count(*) AS count,sum(typeof(command_sequence)<>'integer' "
                "OR typeof(created_at_ms)<>'integer' "
                "OR typeof(turn_number) NOT IN ('integer','null') "
                "OR typeof(delivered_at_ms) NOT IN ('integer','null') "
                "OR typeof(finished_at_ms) NOT IN ('integer','null')) AS bad_types "
                "FROM executor_commands"
            ),
        }
        result = {
            name: [dict(row) for row in connection.execute(sql)] for name, sql in queries.items()
        }
        assert result["total"][0]["bad_types"] == 0
        assert all(
            value in ("integer", "null")
            for rows in result.values()
            for row in rows
            for key, value in row.items()
            if key.endswith("_type")
        )
        return result


def _fact(identity: str, operation: str, status: str, kind: str = "request") -> LifecycleFact:
    return LifecycleFact(
        LifecycleKind(kind), identity, TURN, LifecycleOperation(operation), LifecycleStatus(status)
    )


def _backup(source: Path, destination: Path) -> None:
    with (
        closing(sqlite3.connect(source)) as origin,
        closing(sqlite3.connect(destination)) as target,
    ):
        origin.backup(target)


def _exact_command(command: dict[str, Any], sequence: int, at: int, size: int) -> dict[str, Any]:
    command["arguments"]["content"] = [{"text": ""}]

    def body() -> bytes:
        return wire.encode_bounded(
            wire.MailboxCommand(
                sequence, command["command_id"], at, child_command=_typed("ChildCommand", command)
            )
        )

    command["arguments"]["content"][0]["text"] = "x" * (size - len(body()))
    assert len(body()) == size
    return command


def _big_statements(path: Path) -> list[dict[str, Any]]:
    with closing(connect(str(path))) as connection:
        row = connection.execute(
            "SELECT command_json FROM executor_commands WHERE command_id='withdrawn-recover'"
        ).fetchone()
        command = _typed("MailboxCommand", json.loads(row[0]))
    rewritten = replace(
        command,
        child_command=replace(
            command.child_command,
            turn=_typed("TurnToken", {"conversation_id": "recover", "turn_number": BIG}),
        ),
    )
    return [
        _statement("UPDATE sqlite_sequence SET seq=? WHERE name='executor_commands'", BIG),
        _statement(
            "UPDATE mac_runs SET turn_number=? WHERE conversation_id IN ('basic','recover')", BIG
        ),
        _statement(
            "UPDATE executor_commands SET turn_number=?,command_json=? "
            "WHERE command_id='withdrawn-recover'",
            BIG,
            wire.encode_bounded(rewritten).decode(),
        ),
        _statement(
            "UPDATE conversation_events SET sequence=sequence+? WHERE conversation_id='recover'",
            BIG,
        ),
    ]


def _seed(path: Path) -> None:
    with closing(connect(str(path))) as connection:
        create_schema(connection)
        lease = replace(lease_request(mac_identity(OLD)), mac_wall_clock_ms=NOW)
        accept_lease_in_transaction(connection, lease, NOW)
        for conversation in CONVERSATIONS:
            connection.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,created_at) "
                "VALUES (?,'codex','fixture','/tmp','full',1)",
                (conversation,),
            )
            apply_facts(connection, conversation, (_fact(str(TURN), "turn", "running", "turn"),))
        apply_facts(
            connection,
            "expiry",
            (
                _fact(f"{TURN}:permission:ask", "permission", "pending"),
                _fact(f"{TURN}:user_input:question", "user_input", "answering"),
            ),
        )
        connection.commit()
    store = ExecutorStore(str(path))
    for conversation in CONVERSATIONS:
        store.register_child(
            _typed("ChildReference", _child(conversation)), "ticket", "fixture", NOW
        )
    with closing(connect(str(path))) as connection:
        connection.execute(
            "INSERT INTO executor_incarnations VALUES (?,?,?)",
            (HISTORICAL, NOW - 30000, NOW - 1000),
        )

        def history(
            command: dict[str, Any],
            state: str,
            age: int,
            finished: int | None = None,
            result: dict[str, Any] | None = None,
        ) -> None:
            placed = SqliteCommandMailbox._insert(
                connection,
                wire.MailboxCommand(
                    0,
                    command["command_id"],
                    NOW - age,
                    child_command=_typed("ChildCommand", command),
                ),
                executor_incarnation=HISTORICAL,
                conversation_id=command["child"]["conversation_id"],
                child_id=command["child"]["child_id"],
                turn_number=TURN,
            )
            outcome = None if result is None else _outcome_json(_typed("CommandResult", result))
            connection.execute(
                "UPDATE executor_commands SET state=?,delivered_at_ms=?,"
                "finished_at_ms=?,outcome_json=? WHERE command_id=?",
                (
                    state,
                    NOW - age if state != "waiting" else None,
                    finished,
                    outcome,
                    placed.command_id,
                ),
            )

        for conversation in ("recover", "cancelled"):
            command = _command(
                "withdrawn-" + conversation,
                conversation=conversation,
                child=_child(conversation, HISTORICAL, "-old"),
            )
            history(
                command,
                "withdrawn",
                30000,
                NOW - 1000,
                _result(command, "refused", reason="executor_process_replaced"),
            )
        history(
            _command(
                "historical-cancel",
                "cancel_running_turn",
                "cancelled",
                child=_child("cancelled", HISTORICAL, "-old"),
            ),
            "finished",
            20000,
        )
        for name, state, finished, disposition in (
            ("timely", "finished", NOW - 19999, "succeeded"),
            ("late-open", "delivered", None, "succeeded"),
            ("late-refused", "finished", NOW - 10000, "refused"),
        ):
            command = _command(
                name, "steer", "recover", child=_child("recover", HISTORICAL, "-old")
            )
            history(
                command,
                state,
                20000,
                finished,
                None if state == "delivered" else _result(command, disposition),
            )

        events: list[tuple[Any, str]] = [
            (PromptDeliveryUncertainEventPayload, sender)
            for sender in ("open", "refused", "resolved", "unused")
        ]
        events.append((PromptEventPayload, "resolved"))
        for index, (constructor, sender) in enumerate(events):
            payload = constructor(
                content=text_message_content("work é"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
                sender_message_id=sender,
            )
            connection.execute(
                "INSERT INTO conversation_events VALUES (?,?,?,?,?)",
                (
                    "recover",
                    10 + index,
                    payload.kind.value,
                    conversation_event_payload_to_canonical_json(payload),
                    NOW // 1000,
                ),
            )

        legacy = wire.MailboxCommand(
            0, "archive", NOW - 86400000, model_list_request=wire.ModelListRequest("codex", False)
        )
        connection.executemany(
            "INSERT INTO executor_commands(command_sequence,command_id,executor_incarnation,"
            "operation,command_json,state,created_at_ms,finished_at_ms,outcome_json) "
            "VALUES (?,?,?,'read_model_list',?,'finished',?,?,?)",
            (
                (
                    index + 7,
                    name,
                    HISTORICAL,
                    wire.encode_bounded(
                        replace(legacy, command_sequence=index + 7, command_id=name)
                    ).decode(),
                    legacy.created_at_vps_ms,
                    NOW - 1000,
                    _outcome_json(wire.CommandResult(name, model_list={})),
                )
                for index in range(ARCHIVED)
                for name in (f"archive-{index}",)
            ),
        )
        connection.commit()


class _Source:
    def __init__(self, path: Path) -> None:
        self.path, self.now, self.signals = path, NOW, 0
        self.store = ExecutorStore(str(path))
        self.tasks: dict[str, asyncio.Task[Any]] = {}
        self.trace: list[Any] = []
        self.throw_on: str | None = None
        self.before: list[dict[str, Any]] | None = None
        self.abort = False
        self.after_put: dict[str, Any] | None = None
        self.reentered: Any = None
        self.caller: asyncio.Task[Any] | None = None
        self.ready: asyncio.Future[None] | None = None
        self.fetches = 0
        self.loop = asyncio.get_running_loop()
        self.forget = change_signal.subscribe(self._signal)
        self._owner()
        self.presence: MacPresenceService | None = None
        self.presence_mode = ""
        self.callback_trace: list[Any] = []
        self.callback_gate = asyncio.Event()
        self.change_reads: list[Any] = []

    def _signal(self) -> None:
        self.signals += 1
        if self.presence is not None:
            with closing(connect(str(self.path))) as connection:
                self.change_reads.append(
                    wire.encode_value(
                        dict(
                            health=self.presence.health(),
                            present=self.presence.is_present(),
                            record=self.presence.record(),
                            sql=dict(
                                connection.execute("SELECT * FROM mac_executor_presence").fetchone()
                            ),
                        )
                    )
                )

    def _owner(self) -> None:
        self.mailbox = SqliteCommandMailbox(str(self.path), clock=lambda: self.now)
        self.stalls = self.mailbox.stall_source()
        self.mailbox.settled_listener = self._listener
        original_insert = self.mailbox._insert_child_command

        def insert(
            command: wire.ChildCommand, now: int
        ) -> wire.MailboxCommand | wire.CommandOutcome:
            if self.before is not None:
                statements, self.before = self.before, None
                self._sql(statements)
            nested, abort, caller = self.after_put, self.abort, self.caller
            self.after_put, self.abort = None, False
            result = original_insert(command, now)
            if nested is not None:
                self.reentered = asyncio.run_coroutine_threadsafe(
                    self.mailbox.put_child_command(_typed("ChildCommand", nested)), self.loop
                ).result(timeout=5)
            if abort:
                assert caller is not None
                self.loop.call_soon_threadsafe(caller.cancel, "mailbox fixture observer")
            return result

        self.mailbox._insert_child_command = insert  # type: ignore[method-assign]
        for name in ("_fetch", "_settled_row", "_record"):
            original = getattr(self.mailbox, name)

            def observe(
                *args: Any, original: Any = original, fetch: bool = name == "_fetch"
            ) -> Any:
                if fetch:
                    self.fetches += 1
                result = original(*args)
                ready = self.ready
                if ready is not None:
                    self.loop.call_soon_threadsafe(
                        lambda: None if ready.done() else ready.set_result(None)
                    )
                return result

            setattr(self.mailbox, name, observe)

    def _listener(self, command: wire.MailboxCommand, result: wire.CommandResult) -> None:
        self.trace.append({"id": command.command_id, "result": wire.encode_value(result)})
        if command.command_id == self.throw_on:
            raise RuntimeError("fixture listener failure")

    def _sql(self, statements: list[dict[str, Any]]) -> None:
        with self.store._transaction() as connection:
            for statement in statements:
                connection.execute(statement["sql"], statement["values"])

    async def _call(self, method: str, args: list[Any]) -> Any:
        methods = {
            "put": ("put_child_command", ("ChildCommand",)),
            "model": ("put_model_list_request", ("ModelListRequest",)),
            "poll": ("poll", ("ExecutorIdentity",)),
            "wait": ("wait_result", ()),
            "report": ("record_results", ("ExecutorIdentity", "CommandOutcomeReport")),
            "withdraw": ("withdraw_waiting", ()),
            "proof": ("finish_by_proof", ()),
            "high": ("high_sequence", ()),
        }
        name, roots = methods[method]
        values = list(args)
        for index, root in enumerate(roots):
            values[index] = _typed(root, values[index])
        if method == "put":
            self.caller = asyncio.current_task()
        if method == "withdraw":
            values[1] = CommandSettlementReason(values[1])
        if method == "proof":
            values[1] = _typed("CommandOutcome", values[1])
        result = getattr(self.mailbox, name)(*values)
        return await result if inspect.isawaitable(result) else result

    def _callback_error(self) -> None:
        if self.presence_mode == "syntax":
            json.loads("{")
        if self.presence_mode == "validation":
            _typed("MacUpdateCheck", {})
        kinds = {
            "os": OSError,
            "value": ValueError,
            "conflict": UpdateConflict,
            "type": TypeError,
            "sqlite": sqlite3.OperationalError,
        }
        if self.presence_mode in kinds:
            raise kinds[self.presence_mode]("fixture callback")

    def _presence_owner(self) -> None:
        self.presence = MacPresenceService(
            str(self.path), self.mailbox, application_revision="fixture", clock=lambda: self.now
        )

        def update() -> Any:
            self.callback_trace.append(["update"])
            if self.presence_mode != "sqlite":
                self._callback_error()
            return no_update_check()

        async def replaced(old: str, current: str) -> None:
            self.callback_trace.append(["replace", old, current])
            if self.presence_mode == "hold_replace":
                await self.callback_gate.wait()
            self._callback_error()

        self.presence.update_check = update
        self.presence.incarnation_replaced = replaced
        self.presence.returned = lambda: self.callback_trace.append(["returned"])
        original_contact = self.presence._write_contact

        def write_contact(at_ms: int) -> None:
            if self.presence_mode == "fail_contact":
                self.callback_trace.append(["contact_failure"])
                raise OSError("fixture contact flush")
            original_contact(at_ms)

        self.presence._write_contact = write_contact  # type: ignore[method-assign]

    async def _presence_control(self, action: dict[str, Any]) -> Any:
        method, args = action["method"], action.get("args", [])
        if method == "open":
            self._presence_owner()
            self.callback_trace = []
            return None
        p = self.presence
        assert p is not None
        if method == "mode":
            self.presence_mode = action["mode"]
            self.callback_gate = asyncio.Event()
        elif method == "release":
            self.callback_gate.set()
        elif method == "lease":
            return await p.accept_lease(_typed("LeaseRequest", args[0]))
        elif method == "contact":
            p.record_contact()
        elif method == "refuse":
            await p.refuse_old_websocket()
        elif method == "fence":
            return p.require_current(**args[0])
        elif method == "activity":
            p.note_conversation_activity(action["key"])
        elif method == "drain":
            await p.wait_until_drained(args[0] if args else action["key"])
        elif method == "callbacks":
            return self.callback_trace
        elif method == "timed_lease":
            began = time.perf_counter()
            reply = await p.accept_lease(_typed("LeaseRequest", args[0]))
            return dict(reply=reply, elapsed_ms=(time.perf_counter() - began) * 1000)
        elif method == "cache_only":
            p.incarnation_replaced = None
            p.returned = None
        elif method == "cursor_race":
            before = self.mailbox.high_sequence()
            placed = await asyncio.to_thread(
                self.mailbox._insert_child_command, _typed("ChildCommand", args[1]), self.now
            )
            reply = await p.accept_lease(_typed("LeaseRequest", args[0]))
            return dict(
                placed=placed, reply=reply, cached=self.mailbox.high_sequence(), before=before
            )
        elif method == "mailbox_stop":
            await self.mailbox.close()
        elif method == "state":
            with closing(connect(str(self.path))) as connection:
                waits = p.stall_source().tracked_waits(connection, self.now)
            return dict(
                record=p.record(),
                health=p.health(),
                judgement=p.judgement(),
                identity=p.current_identity(),
                present=p.is_present(),
                waits=waits,
                callbacks=self.callback_trace,
            )
        elif method == "cached":
            with patch("planner.executor.vps_presence.connect", wraps=connect) as opens:
                p.record()
                p.health()
                p.judgement()
                p.current_identity()
                p.is_present()
                return opens.call_count
        elif method == "stored":
            with closing(connect(str(self.path))) as connection:
                return read_presence(connection)
        elif method == "reopen":
            await p.close()
            self._presence_owner()
        elif method == "stop":
            await p.close()
        elif method == "arm":
            original = p._write_lease

            def write(request: Any) -> Any:
                p._write_lease = original  # type: ignore[method-assign]
                result = original(request)
                if action.get("mode") == "stop":
                    asyncio.run_coroutine_threadsafe(p.close(), self.loop).result(5)
                if action.get("mode") == "clock":
                    self.now = action["now"]
                if action.get("nested"):
                    self.reentered = asyncio.run_coroutine_threadsafe(
                        p.accept_lease(_typed("LeaseRequest", action["nested"])), self.loop
                    ).result(5)
                return result

            p._write_lease = write  # type: ignore[method-assign]
        return None

    async def execute(self, action: dict[str, Any]) -> Any:
        op = action["op"]
        if op == "presence":
            return await self._presence_control(action)
        if op == "presence_begin":
            task = asyncio.create_task(_outcome(lambda: self._presence_control(action)))
            self.tasks[action["key"]] = task
            await asyncio.sleep(0.02)
            return {"pending": not task.done()}
        if op == "call":
            return await self._call(action["method"], action["args"])
        if op == "begin":
            self.ready = self.loop.create_future()
            task = asyncio.create_task(
                _outcome(lambda: self._call(action["method"], action["args"]))
            )
            self.tasks[action["key"]] = task
            await asyncio.wait_for(self.ready, 5)
            self.ready = None
            return {
                "pending": not task.done(),
                "waiter": self.mailbox.has_waiter(action["args"][0])
                if action["method"] == "wait"
                else None,
            }
        if op == "join":
            return await asyncio.wait_for(asyncio.shield(self.tasks[action["key"]]), 5)
        if op == "pending":
            return {key: not task.done() for key, task in self.tasks.items()}
        if op == "clock":
            self.now = action["now"]
        elif op == "sql":
            self._sql(action["statements"])
        elif op == "arm":
            self.before, self.abort = action.get("before_sql"), action.get("after_abort", False)
            self.after_put = action.get("after_put")
        elif op == "reentrant":
            return self.reentered
        elif op == "listen":
            self.throw_on, self.trace = action["throw_on"], []
        elif op == "reopen":
            await self.mailbox.close()
            self._owner()
        elif op == "snapshot":
            return {"trace": self.trace, "signals": self.signals}
        elif op == "timed_poll":
            start = time.perf_counter()
            reply = await self._call("poll", action["args"])
            polled_at = time.perf_counter()
            wire_bytes = len(wire.encode_bounded(reply, limit=4 * 1024 * 1024))
            encoded_at = time.perf_counter()
            return {
                "reply": reply,
                "timing": {
                    "poll_ms": (polled_at - start) * 1000,
                    "encode_ms": (encoded_at - polled_at) * 1000,
                    "full_ms": (encoded_at - start) * 1000,
                    "wire_bytes": wire_bytes,
                },
            }
        elif op == "poll_boundaries":
            args = action["args"]
            for _ in range(5):
                await self._call("poll", [*args, 0.002])
                assert not self.mailbox._put_event._waiters
            cancelled = []
            for custom in (False, True, "overflow"):
                self.ready = self.loop.create_future()
                task = asyncio.create_task(
                    self._call("poll", [*args, (2**31 / 1000) if custom == "overflow" else 30])
                )
                await asyncio.wait_for(self.ready, 5)
                self.ready = None
                reason = RuntimeError("fixture abort reason")
                task.cancel(reason) if custom is True else task.cancel()
                try:
                    await task
                    raise AssertionError("cancelled poll returned")
                except asyncio.CancelledError as error:
                    if custom != "overflow":
                        cancelled.append(
                            {
                                "cancelled": True,
                                "wrapped": isinstance(error, asyncio.CancelledError),
                                "reason_preserved": not custom or error.args[0] is reason,
                            }
                        )
            return {
                "cancelled": cancelled,
                "idle_wakes": len(self.mailbox._put_event._waiters),
                "abort_subscribers": 0,
                "overflow": {"accepted_before_cancel": True},
            }
        elif op == "inspect":
            child = _typed("ChildReference", action["child"])
            with closing(connect(str(self.path))) as connection:
                stalls = self.mailbox.stall_source().tracked_waits(connection, self.now)
            return {
                "open": self.mailbox.open_command_ids_of(child),
                "withdrawn": self.mailbox.prompt_withdrawn_at_replacement(child, action["turn"]),
                "rewritten": self.mailbox.prompts_written_again(),
                "late": self.mailbox.late_steers_left_by_a_restart(),
                "start": self.mailbox.start_of(child),
                "input": self.mailbox.command_for_input(action["key"]),
                "row": self.mailbox.command_row(action["key"]),
                "stalls": stalls,
                "high": self.mailbox.high_sequence(),
            }
        elif op == "replace":
            with self.store._transaction() as connection:
                for statement in action["statements"]:
                    connection.execute(statement["sql"], statement["values"])
                settled = self.mailbox.replace_incarnation_in_transaction(
                    connection,
                    replaced=action["args"][0],
                    current=action["args"][1],
                    now_ms=self.now,
                )
            self.mailbox.settled(settled)
        elif op == "stop":
            await self.mailbox.close()
            return {"sql_after_seal": "source performs its next fetch before its closed check"}
        elif op == "poststop":
            with self.store._transaction() as connection:
                before = self.stalls.tracked_waits(connection, self.now)
                settled = self.mailbox.replace_incarnation_in_transaction(
                    connection,
                    replaced=action["args"][0],
                    current=action["args"][1],
                    now_ms=self.now,
                )
                after = self.stalls.tracked_waits(connection, self.now)
            return {"before": before, "settled": settled, "after": after}
        elif op == "pending_failure":
            self.ready = self.loop.create_future()
            report = asyncio.create_task(_outcome(lambda: self._call("report", action["args"])))
            await asyncio.wait_for(self.ready, 5)
            self.ready = None
            await self.mailbox.close()
            return {"report": await report, "waiting": self.mailbox.has_waiter(action["key"])}
        return None


async def _outcome(work: Any) -> dict[str, Any]:
    try:
        return {"ok": True, "value": wire.encode_value(await work())}
    except asyncio.CancelledError as error:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            if task.cancelling() != 1 or error.args != ("mailbox fixture observer",):
                raise
            task.uncancel()
        return {"ok": False, "type": "CancelledError", "message": ""}
    except Exception as error:
        result = {"ok": False, "type": type(error).__name__, "message": str(error)}
        if isinstance(error, ValidationError):
            result["issues"] = [
                {"type": issue["type"], "loc": list(issue["loc"])} for issue in error.errors()
            ]
        return result


def test_unit22_mailbox_exchange(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    async def exercise() -> dict[str, Any]:
        started = time.perf_counter()
        seed = tmp_path / "seed.sqlite"
        _seed(seed)
        seed_seconds = time.perf_counter() - started
        paths = [tmp_path / "python.sqlite", tmp_path / "node.sqlite"]
        for path in paths:
            _backup(seed, path)
        copies = [tmp_path / "python-states.sqlite", tmp_path / "node-states.sqlite"]
        source = _Source(paths[0])
        home = tmp_path / "home"
        home.mkdir()
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C.UTF-8",
            "HOME": str(home),
            "TMPDIR": str(tmp_path),
            "CODEX_HOME": str(home / "codex"),
            "CLAUDE_CONFIG_DIR": str(home / "claude"),
            "HERMES_HOME": str(home / "hermes"),
            "PLAN_TEST_MODE": "1",
            "PLAN_SERVER_URL": "http://127.0.0.1:0",
        }
        process = await asyncio.create_subprocess_exec(
            "node",
            str(ROOT / "server/executor/testing/mailbox.ts"),
            cwd=ROOT,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024,
        )
        assert process.stdin is not None and process.stdout is not None
        assert process.stderr is not None
        stderr = asyncio.create_task(process.stderr.read())

        async def node(action: dict[str, Any]) -> dict[str, Any]:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write((_json(action) + "\n").encode())
            await process.stdin.drain()
            line = await asyncio.wait_for(process.stdout.readline(), 8)
            if not line:
                diagnostics = await asyncio.wait_for(asyncio.shield(stderr), 5)
                raise AssertionError(
                    f"Node companion exited: {process.returncode}\n{diagnostics.decode()[-6000:]}"
                )
            result = json.loads(line)
            assert isinstance(result, dict)
            return result

        uuids: dict[str, str] = {}
        admitted: set[str] = set()
        snapshots: list[dict[str, Any]] = []
        observed_errors: list[dict[str, Any]] = []

        def correlated(snapshot: dict[str, Any]) -> dict[str, Any]:
            for row in snapshot["rows"]:
                replacement = uuids.get(row["command_id"])
                if replacement is None:
                    continue
                row["command_id"] = replacement
                for column, root, serialize in (
                    (
                        "command_json",
                        "MailboxCommand",
                        lambda value: wire.encode_bounded(value).decode(),
                    ),
                    ("outcome_json", "CommandResult", _outcome_json),
                ):
                    if row[column] is not None:
                        value = _typed(root, json.loads(row[column]))
                        row[column] = serialize(replace(value, command_id=replacement))
            return snapshot

        def compared(outcome: dict[str, Any]) -> dict[str, Any]:
            if outcome.get("type") == "ValidationError" and outcome["ok"] is False:
                assert isinstance(outcome["message"], str) and outcome["message"]
                return {**outcome, "message": "<validation diagnostic>"}
            return outcome

        async def paired(action: dict[str, Any]) -> Any:
            expected = await _outcome(lambda: source.execute(action))
            actual = await node(action)
            reference_error, native_error = expected, actual
            if action["op"] == "join" and expected["ok"] and actual["ok"]:
                # A joined fixture task returns its held Outcome, not a wire value.
                reference_error, native_error = expected["value"], actual["value"]
                left = {**expected, "value": compared(reference_error)}
                right = {**actual, "value": compared(native_error)}
            else:
                left, right = compared(expected), compared(actual)
            assert right == left, (action.get("method", action["op"]), actual, expected)
            if not reference_error["ok"]:
                observed_errors.append(
                    {
                        "operation": action.get("method", action["op"]),
                        **reference_error,
                        "typescript_message": native_error["message"],
                    }
                )
            return expected.get("value")

        async def call(method: str, *args: Any) -> Any:
            value = await paired({"op": "call", "method": method, "args": args})
            if method == "put" and isinstance(value, dict) and "command_sequence" in value:
                admitted.add(value["command_id"])
            return value

        async def inspect(child: dict[str, Any], key: str = "none") -> Any:
            return await paired({"op": "inspect", "child": child, "turn": TURN, "key": key})

        async def snapshot() -> Any:
            metadata = await paired({"op": "snapshot"})
            expected = correlated(_observe(paths[0]))
            assert _observe(paths[1]) == expected
            assert expected["total"][0]["count"] == BASE + len(admitted)
            snapshots.append({"rows": expected["total"][0]["count"], "states": expected["counts"]})
            return {**expected, **metadata}

        async def report(*results: Any, identity: Any = IDENTITY) -> Any:
            return await call("report", identity, {"outcomes": results})

        async def begin(key: str, method: str, *args: Any) -> None:
            ready = await paired({"op": "begin", "key": key, "method": method, "args": args})
            assert ready["pending"] is True
            if method == "wait":
                assert ready["waiter"] is True

        async def join(key: str) -> Any:
            return await paired({"op": "join", "key": key})

        try:
            assert (await node({"op": "open", "path": str(paths[1]), "now": NOW}))["ok"]
            # Persistence, exact native-default replay, first-result custody and recovery.
            prompt = _command("prompt")
            placed = await call("put", prompt)
            assert placed["command_sequence"] == BASE + 1
            assert await call("put", deepcopy(prompt)) == placed
            await call(
                "put", {**prompt, "arguments": {**prompt["arguments"], "sender_label": "other"}}
            )
            await call(
                "put", _command("stale", turn={"conversation_id": "basic", "turn_number": TURN - 1})
            )
            first = await call("poll", IDENTITY, BASE, 0)
            assert first["commands"][0]["command_sequence"] == BASE + 1
            await paired({"op": "clock", "now": NOW + 1})
            assert await call("poll", IDENTITY, BASE, 0) == first
            await report(_result(prompt, kind="accepted", count=TURN))
            await report(_result(prompt, kind="later"), {"command_id": "prompt"})
            assert await call("wait", "prompt", 0) == _typed_result(
                _result(prompt, kind="accepted", count=TURN)
            )
            await paired({"op": "reopen"})
            assert (await inspect(_child("basic"), "prompt"))["high"] == BASE + 1
            recovery = await inspect(_child("recover", HISTORICAL, "-old"), "withdrawn-recover")
            assert recovery["withdrawn"] is not None and [
                entry[1] for entry in recovery["late"]
            ] == [10, 11]
            assert (await inspect(_child("cancelled", HISTORICAL, "-old")))["withdrawn"] is None
            await call("put", _command("recover-start", "start", "recover"))
            await call("put", _command("recover-prompt", conversation="recover"))
            rewritten = (await inspect(_child("recover")))["rewritten"]
            assert next(entry for entry in rewritten if entry[0]["conversation_id"] == "recover")[
                1:
            ] == [TURN, _typed_result_prompt(prompt), True, True]
            await snapshot()
            # Admission is read inside the real writer. Oversize encoding rolls custody back.
            await paired(
                {
                    "op": "arm",
                    "before_sql": [
                        _statement(
                            "UPDATE executor_child_assignments SET closed_at_ms=? "
                            "WHERE child_id='basic'",
                            NOW,
                        )
                    ],
                }
            )
            refused = await call("put", _command("closed-assignment", "cancel_running_turn"))
            assert refused["disposition"] == "refused"
            assert await call("put", deepcopy(prompt)) == placed
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE executor_child_assignments SET closed_at_ms=NULL "
                            "WHERE child_id='basic'"
                        )
                    ],
                }
            )
            before = await snapshot()
            oversized = _command(
                "oversize", turn={"conversation_id": "basic", "turn_number": TURN + 1}
            )
            oversized["arguments"]["content"] = [{"text": "x" * (1024 * 1024)}]
            await call("put", oversized)
            assert await snapshot() == before
            # Expiry uses current lifecycle facts and the SQL candidate window.
            await paired({"op": "clock", "now": NOW})
            expiry_commands = [
                _command("expired-steer", "steer", "expiry"),
                _command(
                    "missing-turn",
                    "steer",
                    "expiry",
                    turn={"conversation_id": "expiry", "turn_number": TURN + 1},
                ),
                _command("permission-open", "answer_permission_ask", "expiry"),
                _command(
                    "permission-closed",
                    "answer_permission_ask",
                    "expiry",
                    arguments={"ask_id": "closed", "option_id": "allow"},
                ),
                _command("user-open", "answer_user_input", "expiry"),
                _command(
                    "user-closed",
                    "answer_user_input",
                    "expiry",
                    arguments={"request_id": "closed", "answers": []},
                ),
                _command("never-expire", "cancel_running_turn", "expiry"),
            ]
            cursor = await call("high")
            for command in expiry_commands:
                await call("put", command)
            accepted = await call("put", _command("accepted-steer", "steer", "expiry"))
            await paired({"op": "clock", "now": NOW + 9999})
            accepted_reply = await call("poll", IDENTITY, accepted["command_sequence"] - 1, 0)
            assert accepted_reply["commands"][0]["command_id"] == "accepted-steer"
            await paired({"op": "clock", "now": NOW + 10000})
            reply = await call("poll", IDENTITY, cursor, 0)
            assert [c["command_id"] for c in reply["commands"]] == [
                "permission-open",
                "user-open",
                "never-expire",
                "accepted-steer",
            ]
            await paired({"op": "clock", "now": NOW + 10001})
            assert (
                await call("poll", IDENTITY, accepted["command_sequence"] - 1, 0) == accepted_reply
            )
            withdrawn = _command("withdrawn-now", "cancel_running_turn", "expiry")
            await call("put", withdrawn)
            assert await call("withdraw", "withdrawn-now", "delivery_withdrawn") is True
            assert await call("withdraw", "withdrawn-now", "delivery_withdrawn") is False
            mixed = await report(
                {"command_id": "missing"},
                _result(expiry_commands[0]),
                _result(withdrawn),
                {"command_id": "permission-open"},
            )
            assert [entry["reason"] for entry in mixed["rejected"]] == [
                "unknown_command",
                "command_expired",
                "command_withdrawn",
                "malformed_result",
            ]
            other_process = await report(_result(expiry_commands[2]), identity=SUCCESSOR)
            assert other_process["rejected"][0]["reason"] == "command_of_another_executor_process"
            await snapshot()
            # Byte budget and SQL LIMIT are distinct boundaries in the same fetch owner.
            cursor = await call("high")
            for index in range(5):
                command = _command(f"large-{index}", conversation="budget")
                if index < 4:
                    command = _exact_command(
                        command,
                        cursor + index + 1,
                        source.now,
                        1024 * 1024 - (20 if index == 3 else 0),
                    )
                await call("put", command)
            timed = {"op": "timed_poll", "args": [IDENTITY, cursor, 0]}
            expected_poll = await _outcome(lambda: source.execute(timed))
            native_poll = await node(timed)
            assert native_poll["value"]["reply"] == expected_poll["value"]["reply"]
            performance = {
                "source": expected_poll["value"]["timing"],
                "candidate": native_poll["value"]["timing"],
                "old_rows": ARCHIVED,
            }
            assert performance["candidate"]["commands"] == 4
            assert performance["candidate"]["wire_bytes"] == 4 * 1024 * 1024 - 2
            assert performance["source"]["wire_bytes"] == performance["candidate"]["wire_bytes"]
            assert (
                sum(
                    len(wire.encode_bounded(_typed("MailboxCommand", command))) + 1
                    for command in native_poll["value"]["reply"]["commands"]
                )
                + 16
                == 4 * 1024 * 1024
            )
            for path, copy in zip(paths, copies, strict=True):
                _backup(path, copy)
            # Decode errors arise only when the source visits the row, even at its byte cutoff.
            sixth = await call(
                "put", _command("after-byte-cutoff", "cancel_running_turn", "budget")
            )
            clean = {row["command_id"]: row["command_json"] for row in _observe(paths[0])["rows"]}
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE executor_commands SET command_json='{' "
                            "WHERE command_id='after-byte-cutoff'"
                        )
                    ],
                }
            )
            assert len((await call("poll", IDENTITY, cursor, 0))["commands"]) == 4
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE executor_commands SET command_json='{' "
                            "WHERE command_id='large-4'"
                        ),
                        _statement(
                            "UPDATE executor_commands SET state='waiting',delivered_at_ms=NULL "
                            "WHERE command_id='large-0'"
                        ),
                    ],
                }
            )
            before_poison = await snapshot()
            assert await call("poll", IDENTITY, cursor, 0) is None
            assert await call("poll", IDENTITY, cursor, 0) is None
            assert await snapshot() == before_poison
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE executor_commands SET command_json=? "
                            "WHERE command_id='after-byte-cutoff'",
                            clean["after-byte-cutoff"],
                        )
                    ],
                }
            )
            assert (await call("poll", IDENTITY, sixth["command_sequence"] - 1, 0))["commands"][0][
                "command_id"
            ] == "after-byte-cutoff"
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE executor_commands SET command_json=? "
                            "WHERE command_id='large-4'",
                            clean["large-4"],
                        )
                    ],
                }
            )
            window = await call("high")
            statements = []
            for index in range(32):
                mailbox_command = wire.MailboxCommand(
                    window + index + 1,
                    f"window-{index}",
                    source.now - 35000,
                    model_list_request=wire.ModelListRequest("codex", False),
                )
                statements.append(
                    _statement(
                        "INSERT INTO executor_commands(command_sequence,command_id,"
                        "executor_incarnation,operation,command_json,state,created_at_ms) "
                        "VALUES (?,?,?,'read_model_list',?,'waiting',?)",
                        mailbox_command.command_sequence,
                        mailbox_command.command_id,
                        OLD,
                        wire.encode_bounded(mailbox_command).decode(),
                        mailbox_command.created_at_vps_ms,
                    )
                )
            await paired({"op": "sql", "statements": statements})
            admitted.update(f"window-{index}" for index in range(32))
            live = await call("put", _command("after-window", "cancel_running_turn", "expiry"))
            assert live["command_sequence"] == window + 33
            assert (await call("poll", IDENTITY, window, 0))["commands"] == []
            assert (await call("poll", IDENTITY, window, 0))["commands"][0][
                "command_id"
            ] == "after-window"
            # Model UUIDs are correlated only at their declared identity fields.
            model_action = {
                "op": "call",
                "method": "model",
                "args": [{"backend_key": "codex", "refresh": False}],
            }
            expected = await _outcome(lambda: source.execute(model_action))
            actual = await node(model_action)
            model_a, model_b = expected["value"], actual["value"]
            assert UUID(model_a["command_id"]).version == UUID(model_b["command_id"]).version == 4
            assert model_a["command_id"] != model_b["command_id"]
            uuids[model_a["command_id"]] = model_b["command_id"]
            admitted.add(model_a["command_id"])
            assert {**model_a, "command_id": model_b["command_id"]} == model_b
            await paired({"op": "clock", "now": source.now + 34999})
            model_poll = {
                "op": "call",
                "method": "poll",
                "args": [IDENTITY, model_a["command_sequence"] - 1, 0],
            }
            assert await _outcome(lambda: source.execute(model_poll)) == {
                "ok": True,
                "value": {"commands": [model_a]},
            }
            assert await node(model_poll) == {"ok": True, "value": {"commands": [model_b]}}
            model_result = {
                "command_id": model_a["command_id"],
                "model_list": {"models": [{"id": "fixture"}]},
            }
            source_report: dict[str, Any] = {
                "op": "call",
                "method": "report",
                "args": [IDENTITY, {"outcomes": [model_result]}],
            }
            candidate_report = deepcopy(source_report)
            candidate_report["args"][1]["outcomes"][0]["command_id"] = model_b["command_id"]
            source_reply = await _outcome(lambda: source.execute(source_report))
            node_reply = await node(candidate_report)
            assert source_reply["value"]["recorded"] == [model_a["command_id"]]
            assert node_reply["value"]["recorded"] == [model_b["command_id"]]
            await paired({"op": "listen", "throw_on": None})
            await snapshot()
            # Fresh arrivals do not read stale lease diagnostics; a held poll does.
            identity_json = _observe(paths[0])["presence"][0]["identity_json"]
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE mac_executor_presence SET identity_json='{}' WHERE singleton=1"
                        )
                    ],
                }
            )
            presence_cursor = await call("high")
            assert (await call("poll", IDENTITY, presence_cursor, 0))["commands"] == []
            await begin("bad-presence", "poll", IDENTITY, presence_cursor, 30)
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE executor_commands SET state='delivered',outcome_json=NULL,"
                            "finished_at_ms=NULL WHERE command_id='late-open'"
                        )
                    ],
                }
            )
            before_presence = await snapshot()
            await paired({"op": "clock", "now": source.now + 15001})
            await call("put", _command("bad-presence-wake", "cancel_running_turn", "expiry"))
            failed_poll = await join("bad-presence")
            assert not failed_poll["ok"] and failed_poll["type"] == "ValidationError"
            after_presence = await snapshot()
            assert [
                row for row in after_presence["rows"] if row["command_id"] != "bad-presence-wake"
            ] == before_presence["rows"]
            assert (await node({"op": "wake_count"}))["value"] == 0
            await paired(
                {
                    "op": "sql",
                    "statements": [
                        _statement(
                            "UPDATE mac_executor_presence SET identity_json=? WHERE singleton=1",
                            identity_json,
                        )
                    ],
                }
            )
            assert (await call("poll", IDENTITY, presence_cursor, 0))["commands"][0][
                "command_id"
            ] == "bad-presence-wake"
            # A listener failure during fetch retains expiry and releases its poll registration.
            failed_expiry = await call("put", _command("poll-listener", "steer", "expiry"))
            await paired({"op": "clock", "now": source.now + 10000})
            await paired({"op": "listen", "throw_on": "poll-listener"})
            assert await call("poll", IDENTITY, failed_expiry["command_sequence"] - 1, 0) is None
            assert (await inspect(_child("expiry"), "poll-listener"))["row"]["state"] == "expired"
            assert (await call("wait", "poll-listener", 0))["child_outcome"]["result"][
                "reason"
            ] == "steer_delivery_limit_passed"
            assert (await node({"op": "wake_count"}))["value"] == 0
            await paired({"op": "listen", "throw_on": None})
            await snapshot()
            # A stale held response marks nothing. A fresh poll is its own evidence.
            evidence_cursor = await call("high")
            stale_at = source.now + 15001
            await begin("stale-evidence", "poll", IDENTITY, evidence_cursor, 30)
            await paired({"op": "clock", "now": stale_at})
            evidence_command = _command("evidence-command", "cancel_running_turn", "expiry")
            await call("put", evidence_command)
            assert (await join("stale-evidence"))["value"] == {"commands": []}
            assert (await inspect(_child("expiry"), "evidence-command"))["row"][
                "state"
            ] == "waiting"
            fresh = await call("poll", IDENTITY, evidence_cursor, 0)
            assert fresh["commands"][0]["command_id"] == "evidence-command"
            await paired({"op": "clock", "now": stale_at + 1})
            assert await call("poll", IDENTITY, evidence_cursor, 0) == fresh
            # Stop success remains unproven. Failed stops remain durable stalls.
            stop = _command("stop", "stop", "stop")
            failed = _command("failed-stop", "stop", "failed")
            await call("put", stop)
            await call("put", failed)
            await report(
                _result(stop, first=True), _result(stop, later=True), _result(failed, "uncertain")
            )
            assert (await inspect(_child("stop"), "stop"))["row"]["state"] == "delivered"
            await call("proof", "stop", _result(stop)["child_outcome"])
            assert (await call("wait", "stop", 0))["child_outcome"]["result"] == {"first": True}
            await call("proof", "stop", _result(stop, "refused")["child_outcome"])
            assert any(
                s["wait_key"] == "failed-stop" for s in (await inspect(_child("failed")))["stalls"]
            )
            # Replacement distinguishes waiting, uncertain and never-journaled deliveries.
            waiting = _command("replace-waiting", "cancel_running_turn")
            await call("put", waiting)
            delivered = await call("put", _command("replace-delivered", "cancel_running_turn"))
            await call("poll", IDENTITY, delivered["command_sequence"] - 1, 0)
            unproven = _command("replace-unproven", "stop", "stop")
            await call("put", unproven)
            await report(_result(unproven, success_before_proof=True))
            statements = [
                _statement(
                    "UPDATE executor_incarnations SET replaced_at_ms=? "
                    "WHERE executor_incarnation=?",
                    stale_at + 1,
                    OLD,
                ),
                _statement(
                    "INSERT INTO executor_incarnations VALUES (?,?,NULL)", NEW, stale_at + 1
                ),
                _statement(
                    "UPDATE mac_executor_presence SET executor_incarnation=?,"
                    "identity_json=?,last_lease_at_ms=? WHERE singleton=1",
                    NEW,
                    _json(SUCCESSOR),
                    stale_at + 1,
                ),
            ]
            for conversation in ("race", "pending"):
                statements.extend(
                    [
                        _statement(
                            "UPDATE mac_runs SET executor_incarnation=?,child_id=?,"
                            "turn_number=? WHERE conversation_id=?",
                            NEW,
                            conversation + "-new",
                            TURN,
                            conversation,
                        ),
                        _statement(
                            "INSERT INTO executor_child_assignments VALUES (?,?,?,?,?,?,NULL)",
                            NEW,
                            conversation + "-new",
                            conversation,
                            "ticket",
                            "fixture",
                            stale_at + 1,
                        ),
                    ]
                )
            await paired({"op": "replace", "statements": statements, "args": [OLD, NEW]})
            await call(
                "poll", IDENTITY, BASE, 0
            )  # An old process cannot settle replaced deliveries.
            await call("poll", SUCCESSOR, delivered["command_sequence"] - 1, 0)
            await snapshot()
            # A real commit survives observer cancellation and wakes a real held poll.
            concurrent = _command(
                "cancelled-observer",
                "cancel_running_turn",
                "race",
                child=_child("race", NEW, "-new"),
            )
            second = {**concurrent, "command_id": "reentered-put"}
            await begin("poll", "poll", SUCCESSOR, await call("high"), 30)
            await begin("wait-first", "wait", concurrent["command_id"], None)
            await begin("wait-second", "wait", concurrent["command_id"], None)
            await paired({"op": "arm", "after_abort": True, "after_put": second})
            await call("put", concurrent)
            reentered = await paired({"op": "reentrant"})
            admitted.update((concurrent["command_id"], second["command_id"]))
            poll = await join("poll")
            assert [command["command_id"] for command in poll["value"]["commands"]] == [
                concurrent["command_id"],
                second["command_id"],
            ]
            assert await call("high") == reentered["command_sequence"]
            await snapshot()
            await report(_result(concurrent), _result(second), identity=SUCCESSOR)
            assert await join("wait-first") == await join("wait-second")
            # Listener failure stops notifications, never the already committed batch.
            one = _command(
                "notify-one", "cancel_running_turn", "race", child=_child("race", NEW, "-new")
            )
            two = {**one, "command_id": "notify-two"}
            await call("put", one)
            await call("put", two)
            await begin("notify-one", "wait", "notify-one", None)
            await begin("notify-two", "wait", "notify-two", None)
            await paired({"op": "listen", "throw_on": "notify-one"})
            await report(_result(one), _result(two), identity=SUCCESSOR)
            assert (await join("notify-one"))["ok"]
            assert (await paired({"op": "pending"}))["notify-two"]
            assert await call("wait", "notify-two", 0) == _typed_result(_result(two))
            await snapshot()
            ahead = await call("high") + 10
            assert (await call("poll", SUCCESSOR, ahead, 0))["commands"] == []
            assert await call("high") == ahead - 10
            boundaries = {"op": "poll_boundaries", "args": [SUCCESSOR, await call("high")]}
            reference_boundaries = (await _outcome(lambda: source.execute(boundaries)))["value"]
            native_boundaries = (await node(boundaries))["value"]
            for key in ("cancelled", "idle_wakes", "abort_subscribers"):
                assert native_boundaries[key] == reference_boundaries[key]
            assert reference_boundaries["overflow"] == {"accepted_before_cancel": True}
            assert (
                not native_boundaries["overflow"]["ok"]
                and native_boundaries["overflow"]["type"] == "ValueError"
            )
            postseal = {**one, "command_id": "postseal-waiting"}
            await call("put", postseal)
            await snapshot()
            await begin("close-poll", "poll", SUCCESSOR, await call("high"), 30)
            source_fetches = source.fetches
            source_stop = await _outcome(lambda: source.execute({"op": "stop"}))
            node_stop = await node({"op": "stop", "deadline_ms": 5000})
            assert node_stop == {"ok": True, "value": {"sql_after_seal": 0}}
            assert (await join("close-poll"))["value"] == {"commands": []}
            assert source.fetches - source_fetches == 1
            source_stop["value"]["fetches_after_seal"] = source.fetches - source_fetches
            assert (await join("notify-two"))["type"] == "CancelledError"
            await snapshot()
            sealed = await node(
                {"op": "sealed", "args": [SUCCESSOR, BASE, 0], "command": concurrent}
            )
            assert sealed["value"]["sql"] == 0
            assert all(
                not item["ok"] and item["type"] == "ServerLifecycleError"
                for item in sealed["value"]["refused"]
            )
            stateless = await paired({"op": "poststop", "args": [NEW, OLD]})
            assert any(item["wait_key"] == "postseal-waiting" for item in stateless["before"])
            assert stateless["settled"][0][0]["command_id"] == "postseal-waiting"
            assert not any(item["wait_key"] == "postseal-waiting" for item in stateless["after"])
            await snapshot()
            await paired({"op": "reopen"})
            # Stop retains an unexpected publication failure that was pending at its seal.
            pending = _command(
                "pending-error",
                "cancel_running_turn",
                "pending",
                child=_child("pending", NEW, "-new"),
            )
            await call("put", pending)
            await snapshot()
            await begin("pending-wait", "wait", "pending-error", None)
            await paired({"op": "listen", "throw_on": "pending-error"})
            action = {
                "op": "pending_failure",
                "args": [SUCCESSOR, {"outcomes": [_result(pending)]}],
                "key": "pending-error",
            }
            expected = await _outcome(lambda: source.execute(action))
            actual = await node(action)
            assert (
                actual["value"]["report"]
                == expected["value"]["report"]
                == {"ok": False, "type": "RuntimeError", "message": "fixture listener failure"}
            )
            assert (
                not actual["value"]["stop"]["ok"]
                and actual["value"]["stop"]["type"] == "ShutdownDeadlineExceeded"
            )
            assert actual["value"]["settled"] == {
                "ok": False,
                "type": "AggregateError",
                "message": "Executor mailbox shutdown failed.",
                "errors": [actual["value"]["report"]],
            }
            assert actual["value"]["waiting"] is False
            await join("pending-wait")
            final = await snapshot()
            assert final["total"][0]["count"] == BASE + len(admitted)
            # Peer readers operate on actual producer snapshots, after primary signal scopes end.
            source.forget()
            ids = ["large-4", "accepted-steer", "prompt", "withdrawn-now", "expired-steer"]
            python_reader = SqliteCommandMailbox(str(copies[0]))
            expected_rows = [
                {
                    "row": python_reader.command_row(name),
                    "input": python_reader.command_for_input(name),
                }
                for name in ids
            ]
            assert (await node({"op": "cross_read", "path": str(copies[0]), "ids": ids}))[
                "value"
            ] == wire.encode_value(expected_rows)
            await python_reader.close()
            foreign = SqliteCommandMailbox(str(copies[1]), clock=lambda: source.now)
            peer_rows = [foreign.command_row(name) for name in ids]
            assert [row.state.value for row in peer_rows if row is not None] == [
                "waiting",
                "delivered",
                "finished",
                "withdrawn",
                "expired",
            ]
            for path in copies:
                for row in _observe(path)["rows"]:
                    assert (
                        wire.encode_bounded(
                            _typed("MailboxCommand", json.loads(row["command_json"]))
                        ).decode()
                        == row["command_json"]
                    )
                    if row["outcome_json"] is not None:
                        assert (
                            _outcome_json(_typed("CommandResult", json.loads(row["outcome_json"])))
                            == row["outcome_json"]
                        )
            foreign_signals: list[int] = []
            forget_foreign = change_signal.subscribe(lambda: foreign_signals.append(1))
            try:
                assert all(
                    row is not None and row.command.child_command is not None for row in peer_rows
                )
                results = [
                    _result(wire.as_object(row.command.child_command), reader="python")
                    for row in peer_rows
                    if row is not None
                ]
                settled = await foreign.record_results(
                    mac_identity(OLD), _typed("CommandOutcomeReport", {"outcomes": results})
                )
                assert list(settled.recorded) == ids[:3]
                assert [item.reason for item in settled.rejected] == [
                    "command_withdrawn",
                    "command_expired",
                ]
                assert len(foreign_signals) == 1
                assert (await node({"op": "cross_read", "path": str(copies[1]), "ids": ids}))[
                    "value"
                ] == wire.encode_value(
                    [
                        {"row": foreign.command_row(name), "input": foreign.command_for_input(name)}
                        for name in ids
                    ]
                )
                for row in peer_rows[:2]:
                    assert row is not None
                    result = await foreign.wait_result(row.command.command_id, 0)
                    assert (
                        result is not None
                        and result.child_outcome is not None
                        and result.child_outcome.result == {"reader": "python"}
                    )
                first = await foreign.wait_result("prompt", 0)
                assert (
                    first is not None
                    and first.child_outcome is not None
                    and first.child_outcome.result == {"kind": "accepted", "count": TURN}
                )
                big = _command("big-counter", turn={"conversation_id": "basic", "turn_number": BIG})
                big_report = {"outcomes": [_result(big, count=BIG)]}
                statements = _big_statements(copies[1])
                # Reuse the adapters on the producer copies. Their older row states differ.
                at = source.now
                source.forget()
                await source.mailbox.close()
                await foreign.close()
                source = _Source(copies[1])
                source.now = at
                admitted.clear()
                assert (await node({"op": "open", "path": str(copies[0]), "now": at}))["ok"]
                await paired({"op": "sql", "statements": statements})
                stale = {
                    "op": "call",
                    "method": "put",
                    "args": [
                        {
                            **big,
                            "command_id": "big-stale",
                            "turn": {"conversation_id": "basic", "turn_number": BIG - 1},
                        }
                    ],
                }
                expected_stale = await _outcome(lambda: source.execute(stale))
                actual_stale = await node(stale)
                assert actual_stale == expected_stale
                selected: dict[str, Any] = {
                    "stale": actual_stale,
                    "placed": await call("put", big),
                    "reply": await call("poll", IDENTITY, BIG, 0),
                    "report": await call("report", IDENTITY, big_report),
                    "high": await call("high"),
                }
                read_action = {
                    "op": "inspect",
                    "child": _child("basic"),
                    "turn": BIG,
                    "key": "big-counter",
                }
                expected_read = (await _outcome(lambda: source.execute(read_action)))["value"]
                actual_read = (await node(read_action))["value"]
                assert actual_read["row"] == expected_read["row"]
                assert actual_read["rewritten"] == expected_read["rewritten"]
                assert actual_read["late"] == expected_read["late"]
                selected.update(
                    row=actual_read["row"],
                    rewritten=actual_read["rewritten"],
                    late=actual_read["late"],
                )
                actual_big: dict[str, Any] = {"ok": True, "value": selected}
                assert actual_big["value"]["high"] == BIG + 1
                assert actual_big["value"]["late"][0][1] == BIG + 10
                assert any(entry[1] == BIG for entry in actual_big["value"]["rewritten"])
                for path in copies:
                    observed = _observe(path)
                    row = next(
                        row for row in observed["rows"] if row["command_id"] == "big-counter"
                    )
                    assert (
                        row["sequence"] == str(BIG + 1)
                        and row["sequence_type"] == row["turn_type"] == "integer"
                    )
                    fresh = SqliteCommandMailbox(str(path))
                    read = fresh.command_row("big-counter")
                    assert (
                        read is not None
                        and read.command.child_command is not None
                        and read.command.child_command.turn is not None
                    )
                    assert (
                        read.command.command_sequence == BIG + 1
                        and read.command.child_command.turn.turn_number == BIG
                    )
                    assert wire.encode_bounded(read.command).decode() == row["command_json"]
                    await fresh.close()
                await source.execute({"op": "stop"})
                assert (await node({"op": "stop", "deadline_ms": 5000}))["ok"]
            finally:
                forget_foreign()
                await foreign.close()
            source.forget()
            paths = [tmp_path / "presence-python.sqlite", tmp_path / "presence-node.sqlite"]
            for path in paths:
                _backup(seed, path)
            source = _Source(paths[0])
            assert (await node({"op": "open", "path": str(paths[1]), "now": NOW}))["ok"]
            presence_evidence = await _presence_phases(source, node, paired, paths)
            assert await node({"op": "finish"}) == {"ok": True, "value": None}
            process.stdin.close()
            assert await asyncio.wait_for(process.wait(), 5) == 0
            return {
                "elapsed_seconds": time.perf_counter() - started,
                "seed_seconds": seed_seconds,
                "snapshots": snapshots,
                "errors": observed_errors,
                "snapshot_count": len(snapshots),
                "final_commands": final["total"][0]["count"],
                "performance": performance,
                "poll_boundaries": {"source": reference_boundaries, "candidate": native_boundaries},
                "foreign_states": [row.state.value for row in peer_rows if row is not None],
                "foreign_signal_count": len(foreign_signals),
                "big": actual_big,
                "model_uuid_pairs": uuids,
                "lifecycle_source": source_stop,
                "lifecycle_candidate": node_stop,
                "pending_failure": actual,
                "presence": presence_evidence,
            }
        finally:
            source.forget()
            if source.presence is not None:
                source.callback_gate.set()
                await source.presence.close()
            for task in source.tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*source.tasks.values(), return_exceptions=True)
            await source.mailbox.close()
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await asyncio.wait_for(process.wait(), 5)
            await asyncio.wait_for(stderr, 5)

    result = asyncio.run(asyncio.wait_for(exercise(), 60))
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        report_path = Path(directory) / "unit-22-mailbox.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2) + "\n")


async def _presence_phases(source: _Source, node: Any, paired: Any, paths: list[Path]) -> Any:
    """Reuse the mailbox exchange and populated files for the presence-only gaps."""
    checkpoints = 0

    async def p(method: str, *args: Any, **controls: Any) -> Any:
        value = await paired(dict(op="presence", method=method, args=args, **controls))
        if method in ("lease", "fence", "state", "stored") and not controls.get("expect_error"):
            assert value is not None, (method, args)
        return value

    async def clock(at: int) -> None:
        await paired(dict(op="clock", now=at))

    async def state() -> Any:
        nonlocal checkpoints
        checkpoints += 1
        value = await p("state")
        assert _observe(paths[0]) == _observe(paths[1])
        return value

    async def signal_read() -> Any:
        captured = (await node(dict(op="presence", method="changes")))["value"][-1]
        current = await state()
        assert captured["record"] == captured["sql"] == current["record"]
        assert captured["health"] == current["health"] and captured["present"] == current["present"]
        return captured

    def lease(incarnation: str = OLD, **changes: Any) -> Any:
        value = wire.encode_value(
            replace(lease_request(mac_identity(incarnation)), mac_wall_clock_ms=NOW)
        )
        assert isinstance(value, dict)
        return {**value, **changes}

    async def pending(key: str, method: str, *args: Any) -> None:
        result = await paired(dict(op="presence_begin", method=method, key=key, args=args))
        assert result == {"pending": True}, (
            source.tasks[key].result() if source.tasks[key].done() else result
        )

    await paired(
        dict(
            op="sql",
            statements=[
                _statement("DELETE FROM mac_executor_presence"),
                _statement("DELETE FROM executor_incarnations"),
            ],
        )
    )
    await p("open")
    assert (await state())["health"]["presence"] == "never"
    await p("contact")
    await clock(NOW + 7)
    await p("contact")
    before = await paired(dict(op="snapshot"))
    began = time.perf_counter()
    await asyncio.sleep(10.05)
    contact_seconds = time.perf_counter() - began
    assert (await state())["record"]["last_contact_at_ms"] == NOW + 7
    assert await paired(dict(op="snapshot")) == before
    assert await p("cached") == 0
    # Quiet contact, exact continuity boundaries, sleep retention, and wake.
    for at, changes in (
        (NOW + 10, {}),
        (NOW + 25010, {}),
        (NOW + 50011, {}),
        (NOW + 50012, {"going_to_sleep": True}),
        (NOW + 50013, {"going_to_sleep": True}),
        (NOW + 50014, {}),
    ):
        await clock(at)
        await p("lease", lease(OLD, **changes))
        current = await state()
        assert current["present"] is not changes.get("going_to_sleep", False)
    assert current["record"]["continuous_since_ms"] == NOW + 50011
    lease_signal = await signal_read()
    timer_at = source.now
    before_timer = await paired(dict(op="snapshot"))
    await p("arm", mode="clock", now=timer_at + 59980)
    await p("lease", lease())
    await asyncio.sleep(0.08)
    await clock(timer_at + 60001)
    after_timer = await paired(dict(op="snapshot"))
    assert after_timer["signals"] == before_timer["signals"] + 1
    assert (await state())["judgement"]["presence"] == "not_heard_from"
    # Refusals deduplicate durable evidence; ingress shape failure still records contact.
    for version in (21, 21, 23):
        await clock(source.now + 1)
        assert isinstance(IDENTITY, dict)
        refused = await p("lease", lease(identity={**IDENTITY, "protocol_version": version}))
        assert refused["refusal"]["peer_identity"]["protocol_version"] == version
        await state()
    await p("refuse")
    assert (await state())["record"]["refusal"]["peer_identity"] is None
    refusal_signal = await signal_read()
    assert isinstance(IDENTITY, dict)
    await p(
        "lease",
        lease(identity={**IDENTITY, "executor_incarnation": "not-a-uuid"}),
        expect_error=True,
    )
    await p("lease", lease())
    for protocol, incarnation in (("21", NEW), ("22", NEW), ("22", OLD)):
        await p(
            "fence",
            dict(protocol_header=protocol, incarnation_header=incarnation),
            expect_error=protocol != "22" or incarnation != OLD,
        )
    # Error families differ by callback, not by message text or a synthetic class name.
    for kind in ("conflict", "value", "os", "syntax", "validation", "type"):
        await p("mode", mode=kind)
        await p("lease", lease(), expect_error=kind == "type")
    await p("mode", mode="")
    # A complete fresh lease proves absence; truncated and stale evidence do not.
    waiting = [{"conversation_id": "basic", "oldest_sequence": BIG}]
    working = [
        {
            "child": _child("basic"),
            "turn": {"conversation_id": "basic", "turn_number": BIG},
            "tool_running": False,
        }
    ]
    reply = await p("lease", lease(waiting_conversations=waiting, working_turns=working))
    assert reply is not None and "accepted_at_vps_ms" in reply
    await pending("basic-drain", "drain", "basic")
    first_wait = (await state())["waits"][0]
    assert first_wait["wait_key"] == f"basic:{BIG}"
    await clock(source.now + 1)
    await p("activity", key="basic")
    await p("lease", lease(waiting_conversations_truncated=True, working_turns=working))
    assert (await paired(dict(op="pending")))["basic-drain"]
    assert (await state())["waits"][0]["started_at_ms"] == source.now
    working[0]["tool_running"] = True
    await p("lease", lease(working_turns=working))
    assert (await state())["waits"] == []
    await paired(dict(op="join", key="basic-drain"))
    await clock(source.now + 25000)
    assert await p("drain", key="basic") is None
    await clock(source.now + 1)
    await pending("stale-drain", "drain", "basic")
    await p("lease", lease())
    await paired(dict(op="join", key="stale-drain"))
    # Replacement withdraws waiting work in the same commit and retries owed callbacks.
    for name in ("presence-waiting", "presence-delivered"):
        await paired(dict(op="call", method="put", args=[_command(name)]))
    await paired(dict(op="call", method="poll", args=[IDENTITY, BASE, 0]))
    await paired(dict(op="call", method="put", args=[_command("presence-last")]))
    race_action = dict(
        op="presence", method="cursor_race", args=[lease(), _command("presence-race")]
    )
    source_race = await _outcome(lambda: source.execute(race_action))
    candidate_race = await node(race_action)
    assert source_race["ok"] and candidate_race["ok"]
    native_reply = candidate_race["value"]["reply"]
    reference_reply = source_race["value"]["reply"]
    assert native_reply["mailbox_high_sequence"] == reference_reply["mailbox_high_sequence"] + 1
    assert native_reply == {
        **reference_reply,
        "mailbox_high_sequence": native_reply["mailbox_high_sequence"],
    }
    assert (
        candidate_race["value"]["cached"]
        == candidate_race["value"]["before"]
        == reference_reply["mailbox_high_sequence"]
    )
    await paired(dict(op="call", method="put", args=[_command("presence-race")]))
    await p("mode", mode="sqlite")
    await paired(
        dict(
            op="sql",
            statements=[
                _statement(
                    "UPDATE executor_commands SET executor_incarnation=?,state='waiting',"
                    "finished_at_ms=NULL,outcome_json=NULL "
                    "WHERE command_sequence BETWEEN 7 AND 3006",
                    OLD,
                )
            ],
        )
    )
    measured = dict(op="presence", method="timed_lease", args=[lease(NEW)])
    reference_cost = await _outcome(lambda: source.execute(measured))
    candidate_cost = await node(measured)
    assert reference_cost["ok"] and candidate_cost["ok"]
    assert reference_cost["value"]["reply"] == candidate_cost["value"]["reply"]
    compared = []
    for path in paths:
        with closing(sqlite3.connect(path)) as connection:
            compared.append(
                connection.execute(
                    "SELECT command_sequence,command_id,state,outcome_json,finished_at_ms,"
                    "length(CAST(command_json AS BLOB)) FROM executor_commands "
                    "WHERE command_sequence BETWEEN 7 AND 3006 ORDER BY command_sequence"
                ).fetchall()
            )
    assert compared[0] == compared[1] and len(compared[0]) == 3000
    assert all(row[2] == "withdrawn" for row in compared[0])
    replacement_cost = dict(
        source=reference_cost["value"],
        candidate=candidate_cost["value"],
        rows=3000,
        command_bytes=sum(row[-1] for row in compared[0]),
    )
    await state()
    assert (await p("lease", lease(OLD)))["error"]["code"] == "executor_incarnation_not_current"
    await p("mode", mode="")
    await p("lease", lease(NEW))
    assert ["replace", "", NEW] in (await state())["callbacks"]
    # Delayed replies preserve both source cache branches, including the OR quirk.
    await p("cache_only")
    for initial, successor in (
        (NEW, NEW),
        ("55555555-5555-4555-8555-555555555555", "44444444-4444-4444-8444-444444444444"),
    ):
        await clock(source.now + 1)
        await p("arm", mode="clock", now=source.now + 1, nested=lease(successor, queue_depth=9))
        await p("lease", lease(initial, queue_depth=1))
        await paired(dict(op="reentrant"))
        await state()
    await p("reopen")
    # Older stored lease shape is diagnostic; malformed authoritative fields still fail.
    stored = (await state())["record"]
    for column, text in (("lease_json", "{}"), ("identity_json", "{}"), ("refusal_json", "{}")):
        original = _observe(paths[0])["presence"][0][column]
        await paired(
            dict(
                op="sql",
                statements=[_statement(f"UPDATE mac_executor_presence SET {column}=?", text)],
            )
        )
        expected = await _outcome(lambda: source.execute(dict(op="presence", method="stored")))
        actual = await node(dict(op="presence", method="stored"))
        if column == "lease_json":
            assert actual == expected and actual["value"]["lease"] is None
        else:
            assert not actual["ok"] and not expected["ok"]
            assert actual["type"] == expected["type"] == "ValidationError"
        await paired(
            dict(
                op="sql",
                statements=[_statement(f"UPDATE mac_executor_presence SET {column}=?", original)],
            )
        )
    assert (await p("stored"))["identity"] == stored["identity"]
    # An accepted reply still finishes with the actual mailbox sealed.
    await p("mailbox_stop")
    current_id = (await p("state"))["identity"]["executor_incarnation"]
    await p("lease", lease(current_id))
    # Both owners retain admitted work. Only TS suppresses callbacks after its seal.
    current_id = (await p("state"))["identity"]["executor_incarnation"]
    await p("mode", mode="")
    callbacks_before = await p("callbacks")
    await p("arm", mode="stop")
    old_id, current_id = current_id, "66666666-6666-4666-8666-666666666666"
    expected = await _outcome(
        lambda: source.execute(dict(op="presence", method="lease", args=[lease(current_id)]))
    )
    actual = await node(dict(op="presence", method="lease", args=[lease(current_id)]))
    assert actual == expected
    callback_source = await _outcome(
        lambda: source.execute(dict(op="presence", method="callbacks"))
    )
    callback_candidate = await node(dict(op="presence", method="callbacks"))
    assert callback_source["value"] == [
        *callbacks_before,
        ["replace", old_id, current_id],
        ["returned"],
        ["update"],
    ]
    assert callback_candidate["value"] == callbacks_before
    await p("stop")
    await p("reopen")
    await p("mode", mode="hold_replace")
    current_id = "77777777-7777-4777-8777-777777777777"
    await pending("held-replacement", "lease", lease(current_id))
    await source.execute(dict(op="presence", method="stop"))
    bounded = await node(dict(op="presence", method="stop", deadline_ms=0))
    assert not bounded["ok"] and bounded["type"] == "ShutdownDeadlineExceeded"
    await p("release")
    await paired(dict(op="join", key="held-replacement"))
    await p("stop")
    sealed_lease = await node(dict(op="presence", method="lease", args=[lease(current_id)]))
    assert not sealed_lease["ok"] and sealed_lease["type"] == "ServerLifecycleError"
    # Cold readers and memory-only contact remain usable after stop, without SQL.
    cold_path = paths[1].with_name("empty.sqlite")
    cold_path.touch()
    cold = await node(dict(op="presence_cold", path=str(cold_path)))
    assert cold["ok"] and cold["value"]["touched"] == 0, cold
    assert (
        cold["value"]["read"]["presence"] == "never"
        and cold["value"]["record"]["last_contact_at_ms"] is not None
    )
    assert not cold["value"]["present"]
    await p("reopen")
    await p("mode", mode="fail_contact")
    await clock(source.now + 1)
    await p("contact")
    await asyncio.sleep(10.05)
    assert source.presence is not None and source.presence._contact_flush is not None
    assert isinstance(source.presence._contact_flush.exception(), OSError)
    candidate_attempts = await node(dict(op="presence", method="callbacks"))
    assert candidate_attempts["value"][-1] == ["contact_failure"]
    await p("mode", mode="")
    await p("stop")
    await p("reopen")
    await p("mode", mode="fail_contact")
    await clock(source.now + 1)
    await p("contact")
    source_flush = await _outcome(lambda: source.execute(dict(op="presence", method="stop")))
    candidate_flush = await node(dict(op="presence", method="stop"))
    assert source_flush["ok"] and not candidate_flush["ok"]
    assert candidate_flush["type"] == "AggregateError"
    await source.mailbox.close()
    assert (await node(dict(op="stop")))["ok"]
    return dict(
        checkpoints=checkpoints,
        signal_reads=dict(lease=lease_signal, refusal=refusal_signal, source=source.change_reads),
        replacement_cost=replacement_cost,
        contact_seconds=contact_seconds,
        stopped_callbacks={"source": callback_source, "candidate": callback_candidate},
        cursor_race={"source": source_race, "candidate": candidate_race},
        held_callback_deadline=bounded,
        cold_stop=cold,
        final_flush={"source": source_flush, "candidate": candidate_flush},
    )


def _typed_result(value: Any) -> Any:
    return wire.encode_value(_typed("CommandResult", value))


def _typed_result_prompt(command: dict[str, Any]) -> Any:
    return wire.encode_value(_typed("ChildCommand", command).arguments)
