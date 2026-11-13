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
from uuid import UUID

import pytest
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
from planner.executor import protocol as wire
from planner.executor.contracts import CommandSettlementReason
from planner.executor.store import ExecutorStore
from planner.executor.vps_mailbox import SqliteCommandMailbox
from planner.executor.vps_presence_store import accept_lease_in_transaction

ROOT = Path(__file__).resolve().parents[3]
NOW = 1_000_000
HIGH = 9007199254740993
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
    "delivered_at_ms,finished_at_ms,outcome_json FROM executor_commands ORDER BY command_sequence"
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
        "turn": {"conversation_id": conversation, "turn_number": HIGH},
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
                "CAST(turn_number AS TEXT) AS turn,delivery_uncertain,stop_state "
                "FROM mac_runs ORDER BY conversation_id"
            ),
            "assignments": (
                "SELECT * FROM executor_child_assignments ORDER BY executor_incarnation,child_id"
            ),
            "presence": "SELECT executor_incarnation,last_lease_at_ms FROM mac_executor_presence",
        }
        return {
            name: [dict(row) for row in connection.execute(sql)] for name, sql in queries.items()
        }


def _fact(identity: str, operation: str, status: str, kind: str = "request") -> LifecycleFact:
    return LifecycleFact(
        LifecycleKind(kind), identity, HIGH, LifecycleOperation(operation), LifecycleStatus(status)
    )


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
            apply_facts(connection, conversation, (_fact(str(HIGH), "turn", "running", "turn"),))
        apply_facts(
            connection,
            "expiry",
            (
                _fact(f"{HIGH}:permission:ask", "permission", "pending"),
                _fact(f"{HIGH}:user_input:question", "user_input", "answering"),
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
                turn_number=HIGH,
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
                    HIGH + index,
                    payload.kind.value,
                    conversation_event_payload_to_canonical_json(payload),
                    1,
                ),
            )

        connection.execute("DELETE FROM sqlite_sequence WHERE name='executor_commands'")
        connection.execute("INSERT INTO sqlite_sequence VALUES ('executor_commands',?)", (HIGH,))
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

    def _signal(self) -> None:
        self.signals += 1

    def _owner(self) -> None:
        self.mailbox = SqliteCommandMailbox(str(self.path), clock=lambda: self.now)
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
                self.loop.call_soon_threadsafe(caller.cancel)
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

    async def execute(self, action: dict[str, Any]) -> Any:
        op = action["op"]
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
    except asyncio.CancelledError:
        return {"ok": False, "type": "CancelledError", "message": ""}
    except Exception as error:
        return {"ok": False, "type": type(error).__name__, "message": str(error)}


def test_unit22_mailbox_exchange(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    async def exercise() -> dict[str, Any]:
        started = time.perf_counter()
        seed = tmp_path / "seed.sqlite"
        _seed(seed)
        paths = [tmp_path / "python.sqlite", tmp_path / "node.sqlite"]
        with closing(sqlite3.connect(seed)) as origin:
            for path in paths:
                with closing(sqlite3.connect(path)) as destination:
                    origin.backup(destination)
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

        async def node(action: dict[str, Any]) -> dict[str, Any]:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write((_json(action) + "\n").encode())
            await process.stdin.drain()
            line = await asyncio.wait_for(process.stdout.readline(), 8)
            assert line, f"Node companion exited: {process.returncode}"
            result = json.loads(line)
            assert isinstance(result, dict)
            return result

        uuids: dict[str, str] = {}

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

        async def paired(action: dict[str, Any]) -> Any:
            expected = await _outcome(lambda: source.execute(action))
            actual = await node(action)
            assert actual == expected, (action.get("method", action["op"]), actual, expected)
            return expected.get("value")

        async def call(method: str, *args: Any) -> Any:
            return await paired({"op": "call", "method": method, "args": args})

        async def inspect(child: dict[str, Any], key: str = "none") -> Any:
            return await paired({"op": "inspect", "child": child, "turn": HIGH, "key": key})

        async def snapshot() -> Any:
            metadata = await paired({"op": "snapshot"})
            expected = correlated(_observe(paths[0]))
            assert _observe(paths[1]) == expected
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
            assert placed["command_sequence"] == HIGH + 1
            assert await call("put", deepcopy(prompt)) == placed
            await call(
                "put", {**prompt, "arguments": {**prompt["arguments"], "sender_label": "other"}}
            )
            await call(
                "put", _command("stale", turn={"conversation_id": "basic", "turn_number": HIGH - 1})
            )
            first = await call("poll", IDENTITY, HIGH, 0)
            assert first["commands"][0]["command_sequence"] == HIGH + 1
            await paired({"op": "clock", "now": NOW + 1})
            assert await call("poll", IDENTITY, HIGH, 0) == first
            await report(_result(prompt, kind="accepted", count=HIGH))
            await report(_result(prompt, kind="later"), {"command_id": "prompt"})
            assert await call("wait", "prompt", 0) == _typed_result(
                _result(prompt, kind="accepted", count=HIGH)
            )
            await paired({"op": "reopen"})
            assert (await inspect(_child("basic"), "prompt"))["high"] == HIGH + 1
            recovery = await inspect(_child("recover", HISTORICAL, "-old"), "withdrawn-recover")
            assert recovery["withdrawn"] is not None and [
                entry[1] for entry in recovery["late"]
            ] == [HIGH, HIGH + 1]
            assert (await inspect(_child("cancelled", HISTORICAL, "-old")))["withdrawn"] is None
            await call("put", _command("recover-start", "start", "recover"))
            await call("put", _command("recover-prompt", conversation="recover"))
            rewritten = (await inspect(_child("recover")))["rewritten"]
            assert next(entry for entry in rewritten if entry[0]["conversation_id"] == "recover")[
                1:
            ] == [HIGH, _typed_result_prompt(prompt), True, True]
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
                "oversize", turn={"conversation_id": "basic", "turn_number": HIGH + 1}
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
                    turn={"conversation_id": "expiry", "turn_number": HIGH + 1},
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
                command["arguments"]["content"] = [{"text": "é" * (440 * 1024)}]
                await call("put", command)
            assert len((await call("poll", IDENTITY, cursor, 0))["commands"]) == 4
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
                            HIGH,
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
                "poll", IDENTITY, HIGH, 0
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
            poll = await join("poll")
            assert [command["command_id"] for command in poll["value"]["commands"]] == [
                concurrent["command_id"],
                second["command_id"],
            ]
            assert await call("high") == reentered["command_sequence"]
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
            await begin("close-poll", "poll", SUCCESSOR, await call("high"), 30)
            source_fetches = source.fetches
            source_stop = await _outcome(lambda: source.execute({"op": "stop"}))
            node_stop = await node({"op": "stop", "deadline_ms": 5000})
            assert node_stop == {"ok": True, "value": {"sql_after_seal": 0}}
            assert (await join("close-poll"))["value"] == {"commands": []}
            assert source.fetches - source_fetches == 1
            source_stop["value"]["fetches_after_seal"] = source.fetches - source_fetches
            assert (await join("notify-two"))["type"] == "CancelledError"
            sealed = await node(
                {"op": "sealed", "args": [SUCCESSOR, HIGH, 0], "command": concurrent}
            )
            assert sealed["value"]["sql"] == 0
            assert all(
                not item["ok"] and item["type"] == "ServerLifecycleError"
                for item in sealed["value"]["refused"]
            )
            await paired({"op": "reopen"})
            # Stop retains an unexpected publication failure that was pending at its seal.
            pending = _command(
                "pending-error",
                "cancel_running_turn",
                "pending",
                child=_child("pending", NEW, "-new"),
            )
            await call("put", pending)
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
            assert await node({"op": "finish"}) == {"ok": True, "value": None}
            process.stdin.close()
            assert await asyncio.wait_for(process.wait(), 5) == 0
            return {
                "elapsed_seconds": time.perf_counter() - started,
                "phases": 7,
                "model_uuid_pairs": uuids,
                "lifecycle_source": source_stop,
                "lifecycle_candidate": node_stop,
                "pending_failure": actual,
            }
        finally:
            source.forget()
            await source.mailbox.close()
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await asyncio.wait_for(process.wait(), 5)

    result = asyncio.run(asyncio.wait_for(exercise(), 40))
    directory = request.config.getoption("--compare-report-dir")
    if directory:
        report_path = Path(directory) / "unit-22-mailbox.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, indent=2) + "\n")


def _typed_result(value: Any) -> Any:
    return wire.encode_value(_typed("CommandResult", value))


def _typed_result_prompt(command: dict[str, Any]) -> Any:
    return wire.encode_value(_typed("ChildCommand", command).arguments)
