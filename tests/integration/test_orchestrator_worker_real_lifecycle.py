"""Opt-in real Luna proof of ordinary continuation without Today.

This uses a fresh isolated HTTP server, production Codex factory, normal readiness loop,
and the real Panels CLI inside model turns. It leaves normal provider credentials alone.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import socket
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.runtime.worker_step_readiness_loop import WorkerStepReadinessLoop

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.real_provider_homes
@pytest.mark.skipif(
    os.environ.get("PANELS_REAL_LUNA_LIFECYCLE") != "1",
    reason="set PANELS_REAL_LUNA_LIFECYCLE=1 to run real provider calls",
)
def test_real_luna_worker_continues_recovers_and_replies_without_today(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(asyncio.wait_for(_exercise(tmp_path, monkeypatch), 900))


async def _exercise(runtime: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence_path = Path(os.environ["PANELS_LUNA_LIFECYCLE_EVIDENCE"])
    workspace = runtime / "workspace"
    workspace.mkdir()
    db_path = runtime / "state" / "planner.db"
    db_path.parent.mkdir()
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    (workspace / "AGENTS.md").write_text(
        "This workspace belongs only to an isolated Panels lifecycle proof. "
        "Read Panels skills from this workspace's .agents/skills directory. "
        "Use panels worker my-ticket brief to inspect the current Ticket. "
        "Submit the current field by piping its text into panels worker propose. "
        "The explicit reply requirement uses panels send-message. "
        "Do not inspect or edit other workspaces.\n"
    )
    monkeypatch.setenv("PATH", f"{ROOT / '.venv' / 'bin'}:{os.environ['PATH']}")
    # Login shells reset PATH. This proof-only shell function keeps the exact Panels
    # command spelling while recording which installed CLI actually executes it.
    cli_executable = ROOT / ".venv" / "bin" / "panels"
    cli_receipts = workspace / "cli-executable-receipts.txt"
    shell_environment = workspace / "proof-bash-env"
    quoted_cli = shlex.quote(str(cli_executable))
    quoted_receipts = shlex.quote(str(cli_receipts))
    shell_environment.write_text(
        "panels() {\n"
        f"  printf '%s\\n' {quoted_cli} >> {quoted_receipts}\n"
        f'  {quoted_cli} "$@"\n'
        "}\nexport -f panels\n"
    )
    monkeypatch.setenv("BASH_ENV", str(shell_environment))
    for name in ("PLAN_ACTOR", "PLAN_TICKET_ID", "PLAN_SPRINT_ITEM_ID"):
        monkeypatch.delenv(name, raising=False)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server_url = f"http://127.0.0.1:{port}"
    config = load_config(
        path=str(runtime / "absent-config.yaml"),
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_WORKSPACE_ROOT": str(workspace),
            "PLAN_PORT": str(port),
            "PLAN_LOGS_DIR": str(runtime / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(runtime / "dispatcher.lock"),
        },
    )
    clock = build_clock(config)
    app = create_app(config, clock, lambda: connect(str(db_path)))
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        async with original_lifespan(application):
            readiness = WorkerStepReadinessLoop(
                str(db_path),
                clock,
                conversation_system=application.state.conversation_system,
                asyncio_loop=asyncio.get_running_loop(),
            )
            readiness.start(interval=1)
            try:
                yield
            finally:
                await asyncio.to_thread(readiness.stop)

    app.router.lifespan_context = lifespan
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    evidence: dict[str, Any] = {
        "source_sha": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_diff_sha256": hashlib.sha256(
            subprocess.check_output(["git", "diff"], cwd=ROOT)
        ).hexdigest(),
        "runtime": str(runtime),
        "server_url": server_url,
        "backend": "codex",
        "model": "gpt-6-luna",
        "reasoning_effort": "medium",
        "started_at": datetime.now(UTC).isoformat(),
        "provider_home": "normal inherited account, no copied credentials",
        "recovery": "actual Codex /compact, followed by normal Worker wake",
        "cli_executable": str(cli_executable),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "responses": [],
    }

    def keep() -> None:
        snapshot = connect(str(db_path))
        try:
            evidence["tickets"] = [dict(row) for row in snapshot.execute("SELECT * FROM tickets")]
            evidence["conversations"] = [
                dict(row) for row in snapshot.execute("SELECT * FROM conversations")
            ]
            evidence["events"] = [
                dict(row)
                for row in snapshot.execute(
                    "SELECT * FROM conversation_events ORDER BY conversation_id, sequence"
                )
            ]
            evidence["cli_executable_receipts"] = (
                cli_receipts.read_text().splitlines() if cli_receipts.exists() else []
            )
            evidence["calendar_tables"] = [
                str(row[0])
                for row in snapshot.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name IN ('days','day_tickets','sprints','sprint_outcomes')"
                )
            ]
        finally:
            snapshot.close()
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2))

    async def wait_for(predicate: Any, label: str, timeout: float = 180) -> Any:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            answer = await predicate()
            if answer:
                keep()
                return answer
            await asyncio.sleep(0.25)
        keep()
        raise AssertionError(f"timed out: {label}")

    try:

        async def started() -> bool:
            if server_task.done():
                await server_task
            return server.started

        await wait_for(started, "isolated server startup", 30)
        async with httpx.AsyncClient(base_url=server_url, timeout=60) as client:

            async def post(label: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
                response = await client.post(path, json=body)
                evidence["responses"].append(
                    {
                        "label": label,
                        "path": path,
                        "status": response.status_code,
                        "body": response.json(),
                    }
                )
                keep()
                assert response.is_success, response.text
                return cast(dict[str, Any], response.json())

            project = await post("project", "/api/projects", {"name": "Isolated Luna proof"})
            item = await post(
                "orchestrator",
                "/api/items",
                {"title": "Isolated Luna supervisor", "project_id": project["id"]},
            )
            brief = (
                "This is a bounded isolated lifecycle proof. At needs_work_done, "
                "compute 17 * 19 and "
                "propose work_done with exact text LUNA-WORK-323 through panels worker propose. "
                "At needs_consequences, write proof-result.txt in this workspace "
                "with LUNA-CONSEQUENCES-323, "
                "then propose consequences with that exact text. Use the normal Panels CLI. "
                "Do not create other Tickets, change placement, use live systems, "
                "or call any server except "
                f"{server_url}. While a proposal awaits approval, respond to messages only. "
                "For proof messages, use each authenticated reply target once with its nonce."
            )
            ticket = await post(
                "create Worker",
                "/api/tickets",
                {
                    "title": "Luna ordinary lifecycle proof",
                    "worker_type": "general",
                    "employee_backend": "codex",
                    "employee_launch_model": "gpt-6-luna",
                    "brief": brief,
                    "orchestrator": {"kind": "sprint_item", "id": item["id"]},
                },
            )
            ticket_id = ticket["id"]

            async def ticket_at(field: str) -> dict[str, Any] | None:
                response = await client.get(
                    "/api/tickets", params={"id": ticket_id, "detail": "full"}
                )
                row = response.json()
                pending = row.get("pending_proposal")
                return row if pending and pending["field"] == field else None

            async def idle(conversation_id: str) -> bool:
                response = await client.get(f"/api/conversation/conversations/{conversation_id}")
                return response.is_success and not response.json().get("is_running", True)

            await post(
                "owner accepts brief",
                f"/api/tickets/{ticket_id}/accept/brief",
                {
                    "next_ceiling": "needs_work_done",
                    "next_holder": {"kind": "owner", "id": "owner"},
                },
            )
            work = await wait_for(lambda: ticket_at("work_done"), "Luna proposes work_done")
            assert work["pending_proposal"]["body"].strip() == "LUNA-WORK-323"
            conversation_id = work["conversation_id"]
            await wait_for(lambda: idle(conversation_id), "first Worker turn settles")
            await post(
                "owner message",
                "/api/messages/send",
                {
                    "target": {"kind": "ticket", "id": ticket_id},
                    "message": (
                        "Reply once to this owner message via the required Panels command with "
                        "LUNA-OWNER-REPLY-323. Do not change the pending proposal."
                    ),
                },
            )

            async def event_contains(
                text: str,
                kind: str | None = None,
                sender_id: str | None = None,
                recipient_id: str | None = None,
            ) -> bool:
                snapshot = connect(str(db_path))
                try:
                    for row in snapshot.execute("SELECT kind,payload FROM conversation_events"):
                        payload = json.loads(row["payload"])
                        if text not in row["payload"] or (kind is not None and row["kind"] != kind):
                            continue
                        if (
                            sender_id is not None
                            and payload.get("sender", {}).get("id") != sender_id
                        ):
                            continue
                        if (
                            recipient_id is not None
                            and payload.get("recipient", {}).get("id") != recipient_id
                        ):
                            continue
                        return True
                    return False
                finally:
                    snapshot.close()

            await wait_for(
                lambda: event_contains(
                    "LUNA-OWNER-REPLY-323", "message_to_owner", ticket_id, "owner"
                ),
                "actual Worker explicit owner reply",
            )
            await wait_for(lambda: idle(conversation_id), "owner reply turn settles")
            supervisor_prompt = (
                "This is the isolated Luna lifecycle proof. Send exactly one message to your child "
                f"using panels send-message --ticket {ticket_id} --message "
                "'Reply once to your supervisor with LUNA-SUPERVISOR-REPLY-323 "
                "using the exact required Panels target. Do not change the pending proposal.' "
                "Then reply to the owner with LUNA-SUPERVISOR-SENT-323. "
                "Do not create or edit work. "
                f"Only use server {server_url}."
            )
            supervisor = await post(
                "owner starts real supervisor",
                f"/api/items/{item['id']}/conversation/send",
                {
                    "content": [{"piece": "text", "text": supervisor_prompt}],
                    "sender_label": "owner",
                    "backend_key": "codex",
                    "model": "gpt-6-luna",
                    "reasoning_effort": "medium",
                },
            )
            await wait_for(
                lambda: event_contains(
                    "LUNA-SUPERVISOR-REPLY-323", "prompt", ticket_id, item["id"]
                ),
                "Worker reply reaches real supervisor conversation",
            )
            await wait_for(
                lambda: event_contains(
                    "LUNA-SUPERVISOR-SENT-323", "message_to_owner", item["id"], "owner"
                ),
                "supervisor replies to owner",
            )
            await wait_for(lambda: idle(conversation_id), "Worker supervisor reply turn settles")
            await wait_for(
                lambda: idle(supervisor["conversation_id"]), "supervisor conversation settles"
            )
            await post(
                "real Codex compact",
                f"/api/tickets/{ticket_id}/conversation/send",
                {"content": [{"piece": "text", "text": "/compact"}], "sender_label": "owner"},
            )

            async def compacted() -> bool:
                snapshot = connect(str(db_path))
                try:
                    row = snapshot.execute(
                        "SELECT automatically_compacted_through_sequence FROM conversations "
                        "WHERE conversation_id=?",
                        (conversation_id,),
                    ).fetchone()
                    return row is not None and int(row[0]) > 0
                finally:
                    snapshot.close()

            await wait_for(compacted, "provider-confirmed actual compaction")
            await wait_for(lambda: idle(conversation_id), "compaction completes")
            await post(
                "owner accepts work_done",
                f"/api/tickets/{ticket_id}/accept/work_done",
                {
                    "next_ceiling": "needs_consequences",
                    "next_holder": {"kind": "owner", "id": "owner"},
                },
            )
            consequences = await wait_for(
                lambda: ticket_at("consequences"),
                "Luna resumes and proposes consequences after recovery",
            )
            assert consequences["pending_proposal"]["body"].strip() == "LUNA-CONSEQUENCES-323"
            assert (workspace / "proof-result.txt").read_text().strip() == "LUNA-CONSEQUENCES-323"
            assert await event_contains("Your context was compacted", "prompt")
            await wait_for(lambda: idle(conversation_id), "final Worker turn settles")
            done = await post(
                "owner accepts consequences",
                f"/api/tickets/{ticket_id}/accept/consequences",
                {"next_ceiling": "done", "next_holder": {"kind": "owner", "id": "owner"}},
            )
            assert done["stage"] == "done"
            keep()
            assert evidence["calendar_tables"] == []
            assert len(evidence["cli_executable_receipts"]) >= 4
            assert set(evidence["cli_executable_receipts"]) == {str(cli_executable)}
            evidence["result"] = "passed"
            evidence["completed_at"] = datetime.now(UTC).isoformat()
            keep()
    except BaseException as error:
        evidence["error"] = repr(error)
        keep()
        raise
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, 45)
