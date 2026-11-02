"""Opt-in model-awareness proof for API key requests through the real local CLI.

A wire receipt alone cannot prove that the model sees a result. One native Sol high
conversation requests two synthetic keys and writes actual saved/rejected outcomes.
The owner harness alone receives the synthetic secret. This adds no provider matrix.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import subprocess
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.support.real_provider_application import provider_application, require_provider_auth

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
)
from planner.conversation.message_content import text_message_content
from planner.core.db import connect, create_schema
from planner.credentials import data as credential_data
from planner.credentials.contracts import BearerSecret
from planner.credentials.store import DatabaseCredentialResolver, FileCredentialCipher
from planner.runtime.conversation_start import start_ticket_conversation
from planner.runtime.logic.conversation_start_resolution import ConversationStartValues
from planner.tickets import data as ticket_data

ROOT = Path(__file__).resolve().parents[2]
MODEL = "gpt-6.1-sol"
EFFORT = "high"


@pytest.mark.skipif(
    os.environ.get("PANELS_REAL_CREDENTIAL_REQUEST_TESTS") != "1",
    reason="set PANELS_REAL_CREDENTIAL_REQUEST_TESTS=1 for billable native Sol high calls",
)
def test_real_sol_requests_keys_and_observes_saved_and_rejected_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asyncio.run(asyncio.wait_for(_exercise(tmp_path, monkeypatch), timeout=600))


async def _exercise(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import planner

    assert Path(planner.__file__).resolve().is_relative_to(ROOT)
    revision = _git("rev-parse", "HEAD")
    assert _git("status", "--short") == "", "native proof requires clean committed source"
    auth_route = require_provider_auth(ConversationBackendKey.codex)
    evidence_value = os.environ.get("PANELS_CREDENTIAL_REQUEST_EVIDENCE_ROOT")
    assert evidence_value and Path(evidence_value).is_absolute()
    evidence_root = Path(evidence_value)
    evidence_root.mkdir(parents=True, exist_ok=True)
    database = root / "state" / "planner.db"
    database.parent.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text(
        "This folder belongs to one isolated API key request proof. Follow the bounded prompt. "
        "Use only the named local Panels CLI. Do not inspect other workspaces, provider profiles, "
        "or stored secrets. Do not edit Ticket stages.\n"
    )
    # The installed CLI's shebang anchors execution to this worktree and its dependency pins.
    executable = ROOT / ".venv/bin/panels"
    assert executable.is_file()
    for name in tuple(os.environ):
        if name.startswith("PLAN_") and name not in {"PLAN_CODEX_EXECUTABLE"}:
            monkeypatch.delenv(name)
    monkeypatch.setenv("PLAN_DB_PATH", str(database))
    monkeypatch.setenv("PLAN_LOGS_DIR", str(root / "logs"))
    monkeypatch.setenv("PLAN_DISPATCHER_LOCK_PATH", str(root / "dispatcher.lock"))
    monkeypatch.setenv("PLAN_SERVER_CONTROL_SOCKET", str(root / "control.sock"))
    with closing(connect(str(database))) as conn:
        create_schema(conn)
    conversation_id = "native-key-request-" + uuid.uuid4().hex
    receipts: dict[str, Any] = {
        "source_revision": revision,
        "source_status": "",
        "model": MODEL,
        "reasoning_effort": EFFORT,
        "authentication_route": auth_route,
        "cli_executable": str(executable),
        "conversation_id": conversation_id,
        "proof_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_at": datetime.now(UTC).isoformat(),
        "checkpoints": [],
        "passed": False,
    }
    destination = evidence_root / "native-sol-save-reject.json"

    def keep() -> None:
        destination.write_text(json.dumps(receipts, indent=2) + "\n")

    began = time.monotonic()
    try:
        async with provider_application(database, receipts, serve_loopback=True) as (
            client,
            runtime,
        ):
            created = await client.post(
                "/api/tickets",
                json={
                    "title": "Synthetic native credential request proof",
                    "worker_type": "coding",
                    "manager": {"kind": "chief", "id": "chief"},
                },
            )
            assert created.is_success, created.text
            ticket_id = str(created.json()["id"])
            receipts["ticket_id"] = ticket_id
            role = ConversationRoleMaterials(
                role_text=(
                    "You are a bounded synthetic Ticket worker. Execute only the explicit API key "
                    "request proof. An API key request result is a real prompt: read its actual "
                    "outcome and reference, write the requested JSON receipt, and end the turn. "
                    "Never read credential secrets or invent a result."
                ),
                identity_environment_variables=(
                    ("PLAN_ACTOR", "worker"),
                    ("PLAN_TICKET_ID", ticket_id),
                ),
            )
            with closing(connect(str(database))) as conn:
                await start_ticket_conversation(
                    runtime.system,
                    conn,
                    ticket_data.read_ticket(conn, ticket_id),
                    ConversationStartValues(
                        ConversationBackendKey.codex,
                        MODEL,
                        EFFORT,
                        role,
                        workspace,
                        ConversationAccess.full,
                    ),
                    conversation_id=conversation_id,
                    now=int(time.time()),
                )
                record = conn.execute(
                    "SELECT model,reasoning_effort,identity_environment_variables "
                    "FROM conversations WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()
                assert (record["model"], record["reasoning_effort"]) == (MODEL, EFFORT)
                identity = dict(json.loads(record["identity_environment_variables"]))
                assert identity["PLAN_CONVERSATION_ID"] == conversation_id
                assert identity["PLAN_TICKET_ID"] == ticket_id
                receipts["effective_model"] = record["model"]
                receipts["effective_reasoning_effort"] = record["reasoning_effort"]
            for choice in ("save", "reject"):
                request_id, nonce = str(uuid.uuid4()), uuid.uuid4().hex
                receipt_file = workspace / f"{choice}-continuation.json"
                cli_file = workspace / f"{choice}-cli-response.json"
                suggested = "Panels_native_" + uuid.uuid4().hex
                command = (
                    shlex.join(
                        [
                            str(executable),
                            "credential",
                            "request",
                            "--request-id",
                            request_id,
                            "--name",
                            suggested,
                            "--provider",
                            "Synthetic provider",
                            "--use",
                            "Read a synthetic measurement",
                            "--auth-kind",
                            "bearer",
                            "--json",
                        ]
                    )
                    + " > "
                    + shlex.quote(cli_file.name)
                )
                prompt = (
                    f"Run exactly this local CLI command once: {command}\n"
                    "The CLI creates a pending API key request. End this turn after it succeeds. "
                    "Do not wait, poll, resolve the request, or write the continuation file yet. "
                    "When the actual API key request result arrives in a later prompt, create "
                    f"{receipt_file.name} as JSON with exactly these keys: nonce={nonce!r}, "
                    f"request_id={request_id!r}, outcome=the actual received outcome, "
                    "credential_reference=the actual received reference object or null. "
                    "Use the result's reference, which the owner can rename. Never guess it. "
                    "After you write that file, end the turn. Make no provider API call."
                )
                await runtime.system.send(
                    conversation_id,
                    text_message_content(prompt),
                    sender_label="Proof owner",
                    sender_message_id=uuid.uuid4().hex,
                    reply_requested=False,
                )

                async def pending(request_id: str = request_id) -> dict[str, Any] | None:
                    response = await client.get(f"/api/credentials/requests/{request_id}")
                    if response.status_code == 404:
                        return None
                    assert response.status_code == 200, response.text
                    body: dict[str, Any] = response.json()
                    return body if body["state"] == "pending" else None

                request = await _wait(pending, "native CLI creates the authenticated request")
                await _wait_idle(runtime.system.is_running, conversation_id)
                assert cli_file.is_file(), "agent did not retain the actual CLI response"
                assert json.loads(cli_file.read_text())["request_id"] == request_id
                assert not receipt_file.exists(), "model invented an outcome before resolution"
                with closing(connect(str(database))) as conn:
                    row = conn.execute(
                        "SELECT conversation_id,requester_kind,requester_id "
                        "FROM credential_requests "
                        "WHERE request_id=?",
                        (request_id,),
                    ).fetchone()
                    assert tuple(row) == (conversation_id, "ticket", ticket_id)
                if choice == "save":
                    # Neither this name nor this secret appears in the model's initial prompt.
                    actual_name = "Panels_owner_edited_" + uuid.uuid4().hex
                    secret = "synthetic-owner-only-" + uuid.uuid4().hex
                    resolved = await client.post(
                        f"/api/credentials/requests/{request_id}/save",
                        json={
                            "name": actual_name,
                            "value": secret,
                            "expected_revision": None,
                        },
                    )
                else:
                    resolved = await client.post(f"/api/credentials/requests/{request_id}/reject")
                assert resolved.status_code == 200, resolved.text
                result = resolved.json()
                expected_outcome = "saved" if choice == "save" else "rejected"
                assert result["state"] == expected_outcome
                assert result["delivery"]["status"] == "started", result

                async def continuation(receipt_file: Path = receipt_file) -> dict[str, Any] | None:
                    if not receipt_file.is_file():
                        return None
                    try:
                        body: dict[str, Any] = json.loads(receipt_file.read_text())
                    except json.JSONDecodeError:
                        return None
                    return body

                observed = await _wait(continuation, "model records the actual result")
                await _wait_idle(runtime.system.is_running, conversation_id)
                expected_reference = (
                    result["credential"]["reference"] if result["credential"] is not None else None
                )
                assert observed == {
                    "nonce": nonce,
                    "request_id": request_id,
                    "outcome": expected_outcome,
                    "credential_reference": expected_reference,
                }
                events_response = await client.get(
                    f"/api/conversation/conversations/{conversation_id}/events"
                )
                assert events_response.status_code == 200
                events = events_response.json()["events"]
                result_prompts = [
                    event
                    for event in events
                    if event["kind"] == "prompt"
                    and "API key request result:" in str(event["payload"])
                    and request_id in str(event["payload"])
                ]
                assert len(result_prompts) == 1
                if choice == "save":
                    assert secret not in json.dumps(events)
                    metadata = result["credential"]
                    with closing(connect(str(database))) as conn:
                        resolver = DatabaseCredentialResolver(
                            conn,
                            FileCredentialCipher(
                                database.parent / "credential-secrets/key",
                                has_records=True,
                            ),
                        )
                        assert resolver.resolve(
                            credential_data.read_metadata(conn, metadata["id"]).reference
                        ).secret == BearerSecret(secret)
                else:
                    with closing(connect(str(database))) as conn:
                        assert conn.execute("SELECT COUNT(*) FROM credentials").fetchone()[0] == 1
                receipts["checkpoints"].append(
                    {
                        "choice": choice,
                        "request": request,
                        "resolved": result,
                        "model_continuation": observed,
                        "actual_result_prompt": result_prompts[0],
                        "authenticated_request_origin_matches": True,
                    }
                )
                keep()
            receipts["passed"] = True
    finally:
        receipts["seconds"] = round(time.monotonic() - began, 3)
        receipts["finished_at"] = datetime.now(UTC).isoformat()
        keep()


async def _wait(read: Callable[[], Awaitable[dict[str, Any] | None]], label: str) -> dict[str, Any]:
    async with asyncio.timeout(180):
        while True:
            found = await read()
            if found is not None:
                return found
            await asyncio.sleep(0.1)
    raise AssertionError(label)


async def _wait_idle(is_running: Callable[[str], Awaitable[bool]], conversation_id: str) -> None:
    async with asyncio.timeout(180):
        while await is_running(conversation_id):
            await asyncio.sleep(0.1)


def _git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], cwd=ROOT, text=True).strip()
