"""The lease wire preserves backlog, restore cursor, update offers and replacement refusal."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.compare.direct.test_unit22_mac_http import CHILD, IDENTITY, encoded, typed
from tests.support.synthetic_homes import create_synthetic_homes

from planner.executor import mac_presence
from planner.executor.mac_outbox import OutboxBacklog
from planner.executor.protocol import WaitingConversation

ROOT = Path(__file__).resolve().parents[3]


def test_unit22_mac_leases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    update = dict(schema_version=1, target=None, prefetch=None)
    refusal = dict(
        peer_identity=IDENTITY,
        server_identity=IDENTITY,
        reason="replaced",
        refused_at_ms=1700000000000,
    )
    replies = [
        typed(
            "LeaseReply",
            dict(
                accepted_at_vps_ms=1700000000000,
                away_after_seconds=25,
                update=update,
                mailbox_high_sequence=50,
            ),
        ),
        typed(
            "LeaseRefused",
            dict(error={"code": "executor_protocol_unsupported"}, refusal=None, update=update),
        ),
        typed(
            "LeaseRefused",
            dict(error={"code": "executor_incarnation_not_current"}, refusal=refusal, update=None),
        ),
    ]
    replies.insert(1, replies[0])
    workspace_root = tmp_path / "lease-workspaces"
    workspace_root.mkdir()
    workspace_ids = [f"t_{index:08x}" for index in range(65)]
    for ticket_id in workspace_ids:
        (workspace_root / ticket_id).mkdir()
    refresh_entered, refresh_release = asyncio.Event(), asyncio.Event()
    cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()
    workspace_calls: list[list[str]] = []
    cleaned: list[Any] = []
    cleanup_facts: list[bool] = []
    refreshes = 0

    async def refresh_cleanup() -> None:
        nonlocal refreshes
        refreshes += 1
        refresh_entered.set()
        await refresh_release.wait()

    async def workspace_states(ticket_ids: tuple[str, ...]) -> Any:
        workspace_calls.append(list(ticket_ids))
        return typed("WorkspaceStatesReply", dict(
            states=[dict(ticket_id=value, ended_at=None, deleted=False) for value in ticket_ids],
            at_vps_ms=1700000001999 if len(workspace_calls) == 1 else 1700000002999,
        ))

    async def cleanup_workspaces(states: Any, now: int) -> None:
        cleaned.append(dict(states=[dict(ticket_id=state.ticket_id, ended_at=state.ended_at,
                                        deleted=state.deleted) for state in states], now=now))
        cleanup_entered.set()
        await cleanup_release.wait()
    calls: list[str] = []
    offers: list[str] = []
    lowered: list[list[int]] = []
    cursor, replaced = 100, False
    presence = mac_presence.MacPresence(typed("ExecutorIdentity", IDENTITY))
    working = typed(
        "WorkingTurn",
        dict(
            child=CHILD, turn=dict(conversation_id="conversation", turn_number=3), tool_running=True
        ),
    )
    runner = SimpleNamespace(
        paths=SimpleNamespace(artifacts=workspace_root),
        refresh_worker_cleanup=refresh_cleanup,
        cleanup_workspaces=cleanup_workspaces,
        admission_stopped=False,
        working_turns=lambda: (working,),
        cleanup_uncertainties=(typed("WorkerCleanupUncertainty", dict(
            ticket_id="cleanup-ticket", conversation_id="cleanup-conversation",
            reason="Previous Worker cleanup is not proven",
        )),),
        legacy_cleanup_notice="Legacy Worker cleanup requires owner review",
    )
    backlog = OutboxBacklog(
        7,
        11,
        tuple(WaitingConversation(f"conversation-{index}", index + 1) for index in range(64)),
        True,
    )

    async def lease(request: Any) -> Any:
        nonlocal cursor
        calls.append(encoded("LeaseRequest", request))
        cursor += 1
        return replies[len(calls) - 1]

    def lower(high: int, *, cursor_at_lease: int) -> bool:
        lowered.append([high, cursor_at_lease])
        return True

    def replacement() -> None:
        nonlocal replaced
        replaced = True

    loop = mac_presence.MacLeaseLoop(
        client=cast(Any, SimpleNamespace(lease=lease, workspace_states=workspace_states)),
        presence=presence,
        outbox=cast(Any, SimpleNamespace(lease_backlog=lambda: backlog)),
        runner=cast(Any, runner),
        journal=cast(Any, SimpleNamespace(poll_cursor=lambda: cursor, lower_poll_cursor=lower)),
        updates=cast(
            Any,
            SimpleNamespace(offer=lambda value: offers.append(encoded("MacUpdateCheck", value))),
        ),
        replaced=replacement,
    )

    async def exchange() -> list[bool]:
        values = [await loop.lease_once()]
        try:
            await refresh_entered.wait()
            values.append(await loop.lease_once())
            cleanup_facts.append(refreshes == 1 and not cleaned)
            refresh_release.set()
            await cleanup_entered.wait()
            values.append(await loop.lease_once())
            assert loop._workspace_cleanup_task is not None
            cleanup_facts.append(not loop._workspace_cleanup_task.done())
            cleanup_release.set()
            await loop._workspace_cleanup_task
            values.append(await loop.lease_once())
            return values
        finally:
            refresh_release.set()
            cleanup_release.set()

    with monkeypatch.context() as patch:
        patch.setattr(mac_presence, "now_ms", lambda: 1700000000000)
        patch.setattr(os, "getpid", lambda: 7)
        expected = dict(
            calls=calls,
            offers=offers,
            lowered=lowered,
            keep_running=asyncio.run(exchange()),
            replaced=replaced,
        )
        status = json.loads(
            encoded("MacLocalStatus", presence.status(admission_stopped=runner.admission_stopped))
        )
    expected.update(status=status, failed=status.pop("last_lease_failure") is not None)
    expected["workspace"] = dict(calls=workspace_calls, cleaned=cleaned,
                                 facts=cleanup_facts, refreshes=refreshes)
    spec = tmp_path / "leases.json"
    payload = dict(
        identity=encoded("ExecutorIdentity", presence.identity),
        workspace_root=str(workspace_root),
        working=encoded("WorkingTurn", working),
        cleanup_uncertainties=[
            json.loads(encoded("WorkerCleanupUncertainty", item))
            for item in runner.cleanup_uncertainties
        ],
        legacy_cleanup_notice=runner.legacy_cleanup_notice,
        replies=[
            encoded("LeaseReply" if index < 2 else "LeaseRefused", reply)
            for index, reply in enumerate(replies)
        ],
    )
    spec.write_text(json.dumps(payload))
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(create_synthetic_homes(tmp_path))
    environment.update(PLAN_TEST_MODE="1", PLAN_SERVER_URL="http://127.0.0.1:9")
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import{readFileSync}from'node:fs'; const{leases}=await import(process.argv[1]); "
            "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
            "console.log(JSON.stringify(await leases(input)));",
            (ROOT / "server/executor/mac/testing/leases_parity.ts").as_uri(),
            str(spec),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == expected
    assert workspace_calls == [workspace_ids[:64], workspace_ids[64:]]
    assert cleanup_facts == [True, True] and refreshes == 1
    assert cleaned == [dict(
        states=[dict(ticket_id=value, ended_at=None, deleted=False) for value in workspace_ids],
        now=1700000001,
    )]


def test_mac_cleanup_snapshot_populated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Journal attribution and missing-proof fences are absent from lease-wire coverage.

    Reuse the existing direct Mac host. Real private files and the shipped journal
    schema carry synthetic source history. No provider, signal, or TS SQL owner runs.
    The journal API adapter remains a separate integration boundary.
    """
    import dataclasses
    import sqlite3
    import time

    from tests.compare.direct.mac_executor_fixture import (
        paired_fixture,
        report_direct,
        start_inputs,
    )

    from planner.conversation.local_child_contracts import LocalHostBootIdentity
    from planner.execution_identity import ChildReference
    from planner.executor import cleanup_reconciliation, legacy_worker_cleanup
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.contracts import COMMAND_JOURNAL_SCHEMA
    from planner.worker_supervision import records
    from planner.worker_supervision.contracts import InspectionContext, WorkerProcess

    def inputs(_root: Path) -> dict[str, Any]:
        def source(name: str) -> dict[str, str]:
            return dict(executor_incarnation="old", child_id=name, conversation_id=name)

        commands: list[dict[str, str | None]] = []

        def command(
            name: str,
            ticket: str,
            *,
            modern: bool = True,
            stop: bool = False,
            refused: bool = False,
        ) -> None:
            body = start_inputs(tmp_path)[0]
            body["kind"] = "start"
            body["resolved_start"]["conversation_id"] = name
            body["resolved_start"]["role_materials"]["identity_environment_variables"] = [
                ["PLAN_TICKET_ID", ticket]
            ]
            if modern:
                body["supervision_protocol"] = 24
            for operation in ("start", "stop") if stop else ("start",):
                key = name + "-" + operation
                child = dict(
                    command_id=key,
                    child=source(name),
                    operation=operation,
                    turn=None,
                    arguments=body if operation == "start" else {"kind": "stop"},
                )
                raw = dict(
                    command_id=key,
                    command_sequence=len(commands) + 1,
                    created_at_vps_ms=1,
                    child_command=child,
                )
                result = None
                if operation == "stop" or refused:
                    result = json.dumps(
                        dict(
                            command_id=key,
                            child_outcome=dict(
                                command_id=key,
                                child=source(name),
                                disposition="refused" if refused else "succeeded",
                                result={},
                            ),
                        )
                    )
                commands.append(dict(command_json=json.dumps(raw), result_json=result))

        for name, ticket in (
            ("missing", "one"),
            ("legacy", "one"),
            ("stopped", "one"),
            ("refused", "one"),
            ("live", "one"),
            ("pending", "two"),
            ("unreadable", "two"),
        ):
            command(
                name,
                ticket,
                modern=name != "legacy",
                stop=name == "stopped",
                refused=name == "refused",
            )
        processes = [
            dict(pid=pid, birth=str(pid), parent=0, group=pid, session=pid, zombie=False)
            for pid in (1, 20, 30)
        ]
        owner, live, unassigned = processes
        found = []
        for index, (record_name, ticket, root, state) in enumerate(
            (
                ("live", "one", live, "running"),
                ("pending", "two", None, "pending"),
                ("complete", "two", live, "complete"),
                (None, "three", unassigned, "running"),
                ("unreadable", "two", live, "complete"),
            ),
            1,
        ):
            found.append(
                dict(
                    name=f"{index:032x}.json",
                    mode=0o644 if record_name == "unreadable" else 0o600,
                    record=dict(
                        version=1,
                        ticket_id=ticket,
                        conversation_id=record_name,
                        source_incarnation="old" if record_name else None,
                        source_child_id=record_name,
                        host="host",
                        boot="boot",
                        owner=owner,
                        root=root,
                        isolated=False,
                        state=state,
                        processes=[] if root is None else [root],
                    ),
                )
            )
        receipt = dict(
            version=1,
            attempted=True,
            targets=[dict(process_id=101, started="legacy"), dict(process_id=102, started="gone")],
            error="old inspection failed",
        )
        return dict(
            commands=commands,
            runner_start=start_inputs(_root)[0],
            records=found,
            processes=processes,
            steps=[
                dict(label="initial"),
                dict(
                    label="healthy",
                    options=dict(
                        healthy_sources=[source("live")], preparing_sources=[source("pending")]
                    ),
                ),
                dict(label="ticket-only", options=dict(ticket_id="two")),
                dict(label="legacy-attempted", receipt=receipt, legacy=[101]),
                dict(label="legacy-absent", legacy=[]),
                dict(
                    label="revoke-pending",
                    options=dict(revoke_pending=True, healthy_sources=[source("live")]),
                ),
                dict(label="ended", ended=[20, 30], readable=[found[-1]["name"]]),
            ],
        )

    async def reference(root: Path, patch: pytest.MonkeyPatch) -> dict[str, Any]:
        data = inputs(root)
        paths = ExecutorPaths(root)
        journals = paths.run / "worker-processes"
        journals.mkdir(parents=True, mode=0o700)
        paths.spool.mkdir()
        for item in data["records"]:
            records.write(journals / item["name"], item["record"])
            (journals / item["name"]).chmod(item["mode"])
        with sqlite3.connect(paths.spool / "commands.sqlite3") as conn:
            conn.executescript(COMMAND_JOURNAL_SCHEMA)
            for index, item in enumerate(data["commands"], 1):
                raw = json.loads(item["command_json"])
                conn.execute(
                    "INSERT INTO commands(command_id,command_sequence,executor_incarnation,"
                    "mailbox_command_json,phase,result_json,recorded_at_ms) "
                    "VALUES (?,?,'old',?,'finished',?,1)",
                    (raw["command_id"], index, item["command_json"], item["result_json"]),
                )
        table = {item["pid"]: WorkerProcess(**item) for item in data["processes"]}
        native = SimpleNamespace(
            owns_isolated_session_members=False,
            requires_containment=False,
            snapshot=lambda _: dict(table),
            is_absent=lambda process, _: table.get(process.pid) != process,
        )
        patch.setattr(
            "planner.worker_supervision._inspection_context",
            lambda _: InspectionContext(native, LocalHostBootIdentity("host", "boot")),
        )
        legacy: set[int] = set()
        patch.setattr(
            legacy_worker_cleanup, "_started", lambda pid, _: "legacy" if pid in legacy else None
        )
        points = []
        for step in data["steps"]:
            if "receipt" in step:
                (paths.run / legacy_worker_cleanup.RECEIPT).write_text(json.dumps(step["receipt"]))
            if "legacy" in step:
                legacy = set(step["legacy"])
            for pid in step.get("ended", []):
                table.pop(pid)
            for name in step.get("readable", []):
                (journals / name).chmod(0o600)
            options = dict(step.get("options", {}))
            for key in ("healthy_sources", "preparing_sources"):
                if key in options:
                    options[key] = frozenset(ChildReference(**value) for value in options[key])
            snapshot = cleanup_reconciliation.snapshot_cleanup(
                paths, current_incarnation="new", **options
            )
            points.append(
                dict(
                    label=step["label"],
                    uncertainties=[dataclasses.asdict(value) for value in snapshot.uncertainties],
                    sources=[
                        dict(source=dataclasses.asdict(source), ticket_id=ticket)
                        for source, ticket in snapshot.sources.items()
                    ],
                    notice=snapshot.notice,
                )
            )
        # The snapshot proof cannot prove the runner's exemptions, command routing,
        # cache replacement, or the Ticket lock before a slow refresh.
        from dataclasses import replace

        from planner.executor import mac_runner
        from planner.executor.protocol import (
            ChildCommand,
            ChildOperation,
            RecheckCleanup,
            StartRun,
            type_adapter,
        )

        patch.setattr(
            mac_runner, "install_native_skill_snapshot", lambda *args, **kwargs: root / "skills"
        )
        native_starts: list[str] = []

        class Native:
            async def start(self, resolved: Any, **_: Any) -> None:
                native_starts.append(resolved.conversation_id)

            async def stop(self) -> None:
                pass

        runner = mac_runner.MacRunner(
            incarnation="new",
            outbox=cast(Any, None),
            factory=cast(Any, lambda _: lambda **_: Native()),
            message_files=cast(Any, None),
            paths=paths,
            workspace_home=root,
        )
        template = type_adapter(StartRun).validate_python(data["runner_start"])

        def start_command(name: str, ticket: str) -> ChildCommand:
            role = template.resolved_start.role_materials
            assert role is not None
            resolved = replace(
                template.resolved_start,
                conversation_id=name,
                role_materials=replace(
                    role, identity_environment_variables=(("PLAN_TICKET_ID", ticket),)
                ),
            )
            return ChildCommand(
                name,
                ChildReference("new", name, name),
                ChildOperation.start,
                None,
                replace(template, resolved_start=resolved),
            )

        async def recheck(name: str, source: ChildReference, ticket: str) -> dict[str, Any]:
            command = ChildCommand(
                name,
                ChildReference("new", "controller", "controller"),
                ChildOperation.recheck_cleanup,
                None,
                RecheckCleanup(source=source, ticket_id=ticket),
            )
            outcome = await runner.run(command)
            return dict(disposition=outcome.disposition.value, result=outcome.result)

        def cached() -> list[dict[str, Any]]:
            return [dataclasses.asdict(item) for item in runner.cleanup_uncertainties]

        await runner.refresh_worker_cleanup()
        initial = cached()
        blocked = await runner.run(start_command("blocked", "one"))
        clean = start_command("clean", "clean-ticket")
        accepted = await runner.run(clean)
        process = WorkerProcess(40, "40", 1, 40, 40, False)
        table[40] = process
        live_record = dict(
            data["records"][0]["record"],
            ticket_id="clean-ticket",
            conversation_id="clean",
            source_incarnation="new",
            source_child_id="clean",
            root=dataclasses.asdict(process),
            processes=[dataclasses.asdict(process)],
        )
        records.write(journals / ("e" * 32 + ".json"), live_record)
        await runner.refresh_worker_cleanup()
        healthy = cached()
        active = await recheck("active-recheck", clean.child, "clean-ticket")
        active_cache = cached()
        table.pop(40)
        complete = await recheck("complete-recheck", clean.child, "clean-ticket")
        missing = await recheck(
            "missing-recheck", ChildReference("old", "missing", "missing"), "two"
        )
        complete_cache = cached()
        refresh = runner.refresh_worker_cleanup
        entered, release = asyncio.Event(), asyncio.Event()
        refreshes: list[str | None] = []

        async def slow_refresh(**options: Any) -> None:
            refreshes.append(options.get("ticket_id"))
            if options.get("ticket_id") == "slow-ticket" and not entered.is_set():
                entered.set()
                await release.wait()
            await refresh(**options)

        patch.setattr(runner, "refresh_worker_cleanup", slow_refresh)
        slow = asyncio.create_task(runner.run(start_command("slow-first", "slow-ticket")))
        await entered.wait()
        second = asyncio.create_task(runner.run(start_command("slow-second", "slow-ticket")))
        await runner.run(start_command("fast", "fast-ticket"))
        serialized = (
            refreshes.count("slow-ticket") == 1
            and "fast" in native_starts
            and "slow-first" not in native_starts
        )
        release.set()
        first, duplicate = await asyncio.gather(slow, second)
        runner_evidence = dict(
            initial=initial,
            blocked=blocked.disposition.value,
            accepted=accepted.disposition.value,
            healthy=healthy,
            active=active,
            active_cache=active_cache,
            complete=complete,
            missing=missing,
            complete_cache=complete_cache,
            serialized=serialized,
            first=first.disposition.value,
            duplicate=duplicate.disposition.value,
            native_starts=native_starts,
        )
        assert (
            serialized
            and blocked.disposition.value == "refused"
            and duplicate.disposition.value == "refused"
        )
        for child in runner._children.values():
            await child.native.stop()
        return dict(points=points, runner=runner_evidence)

    began = time.perf_counter()
    expected, observed, _ = paired_fixture(
        tmp_path,
        monkeypatch,
        reference=reference,
        inputs=inputs,
        entry="server/executor/mac/testing/cleanup_parity.ts",
        python_timeout=10,
        node_timeout=10,
    )
    assert observed["parity"] == expected
    report_direct(
        request,
        "mac-cleanup-snapshot.json",
        observed,
        began,
        "Private snapshots and populated runner admission, exemptions, exact historical "
        "rechecks, cache replacement, and per-Ticket start serialization. "
        "The same host retains all seven snapshot points and real journal settlement.",
    )
