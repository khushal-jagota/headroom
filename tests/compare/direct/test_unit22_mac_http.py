"""One owned TLS peer proves fresh Python/TS request bytes and transfer/closure boundaries."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import subprocess
import time
from pathlib import Path
from typing import Any, cast

from aiohttp import web
from tests.support.synthetic_homes import create_synthetic_homes

from planner.executor import protocol as p
from planner.executor.diagnostics import ConnectionDiagnostics, ConnectionPhase
from planner.executor.mac_http import ExecutorHttpError, MacHttpClient, failure_name

ROOT = Path(__file__).resolve().parents[3]
INC = "00000000-0000-0000-0000-000000000001"
IDENTITY = dict(
    protocol_version=22,
    application_revision="synthetic",
    spool_format_version=5,
    executor_incarnation=INC,
    connection_generation=0,
)
CHILD = dict(conversation_id="conversation", child_id="child", executor_incarnation=INC)


def typed(name: str, value: Any) -> Any:
    return p.type_adapter(getattr(p, name)).validate_python(value)


def encoded(name: str, value: Any) -> str:
    return p.type_adapter(getattr(p, name)).dump_json(value).decode()


def fixtures(content: bytes) -> tuple[dict[str, str], dict[str, Any]]:
    digest = hashlib.sha256(content).hexdigest()
    manifest = dict(
        scope="conversation",
        owner_id="conversation",
        relative_path="file.txt",
        sha256=digest,
        byte_count=len(content),
    )
    envelope = dict(
        sender_id="sender",
        conversation_id="conversation",
        sequence=17,
        submission_id="submission",
        kind="cli_write",
        file_dependencies=[manifest],
        payload=dict(
            operation="worker.recap", arguments={"text": "populated λ"}, child=CHILD, turn=None
        ),
    )
    roots = {
        "LeaseRequest": dict(
            identity=IDENTITY,
            process_id=1234,
            mac_wall_clock_ms=1700000000123,
            queue_depth=7,
            oldest_item_age_ms=111,
            admission_stopped=False,
            waiting_conversations=[dict(conversation_id="conversation", oldest_sequence=17)],
            waiting_conversations_truncated=False,
            working_turns=[],
            going_to_sleep=False,
        ),
        "CommandOutcomeReport": dict(
            outcomes=[
                dict(
                    command_id="command",
                    child_outcome=None,
                    model_list={"models": ["one", "two"], "number": 9007199254740993},
                )
            ]
        ),
        "ItemBatch": dict(sender_id="sender", items=[dict(envelope=envelope, sender_refusal=None)]),
        "LiveReadRequest": dict(
            child=CHILD, path="/api/tickets", params={"query": "λ", "integer": 9007199254740993}
        ),
        "LiveEventBatch": dict(
            events=[
                dict(
                    child=CHILD,
                    turn=None,
                    method="agent_message_delta",
                    arguments={"text_delta": "live λ"},
                )
            ]
        ),
        "FileUploadRequest": dict(child=CHILD, manifest=manifest),
    }
    replies = {
        "lease": dict(
            accepted_at_vps_ms=1700000000999,
            away_after_seconds=25.0,
            update=dict(schema_version=1, target=None, prefetch=None),
            mailbox_high_sequence=9007199254740993,
        ),
        "refused": dict(
            error=dict(code="executor_incarnation_not_current", message="replaced"),
            refusal=None,
            update=None,
        ),
        "poll": dict(
            commands=[
                dict(
                    command_sequence=9007199254740994,
                    command_id="catalog",
                    created_at_vps_ms=1700000000000,
                    child_command=None,
                    model_list_request=dict(backend_key="codex", refresh=True),
                )
            ]
        ),
        "report": dict(recorded=["command"], rejected=[]),
        "items": dict(
            receipts=[
                dict(
                    sender_id="sender",
                    submission_id="submission",
                    disposition="accepted",
                    result={"saved": True},
                    accepted_at_vps_ms=1700000000123,
                )
            ],
            stop=None,
        ),
        "read": {"data": ["one", "λ"], "integer": 9007199254740993, "float": 1.0},
    }
    return {key: encoded(key, typed(key, value)) for key, value in roots.items()}, replies


async def reference(
    origin: str, ca: Path, roots: dict[str, str], source: Path, target: Path
) -> dict[str, Any]:
    context = ssl.create_default_context(cafile=str(ca))
    client = MacHttpClient(
        origin, "synthetic-executor", typed("ExecutorIdentity", IDENTITY), ssl_context=context
    )
    results: list[Any] = []

    def value(name: str) -> Any:
        return p.type_adapter(getattr(p, name)).validate_json(roots[name])

    async def save(work: Any, name: str | None = None) -> None:
        try:
            answer = await work()
            results.append(encoded(name, answer) if name else answer)
        except ExecutorHttpError as error:
            results.append(
                dict(
                    status=error.status,
                    code=error.code,
                    message=error.message,
                    failure=failure_name(error),
                )
            )
        except Exception as error:
            results.append(dict(failure=failure_name(error)))

    try:
        for index in range(4):
            await save(
                lambda: client.lease(value("LeaseRequest")),
                "LeaseReply" if index == 0 else "LeaseRefused" if index == 1 else None,
            )
        await save(lambda: client.poll_commands(9007199254740993, 1.9), "MailboxPollReply")
        await save(
            lambda: client.report_outcomes(value("CommandOutcomeReport")), "CommandOutcomeReply"
        )
        await save(lambda: client.post_items("conversation", value("ItemBatch")), "ItemBatchReply")
        await save(lambda: client.read(value("LiveReadRequest")), "JsonObject")
        await save(lambda: client.post_live("conversation", value("LiveEventBatch")))
        await save(lambda: client.upload_file(value("FileUploadRequest"), source))
        await save(lambda: client.download_command_file("command", "file", target))
        await save(
            lambda: client.download_command_file(
                "command", "corrupt", Path(str(target) + "-corrupt")
            )
        )
        await save(lambda: client.poll_commands(1, 0))
        await client.reset()
        await save(lambda: client.read(value("LiveReadRequest")), "JsonObject")
    finally:
        await client.close()
    logs: list[str] = []
    now = 0.0
    diagnostics = ConnectionDiagnostics(logs.append, monotonic=lambda: now)
    for _ in range(2):
        diagnostics.report(ExecutorHttpError(403, "denied", "private"), phase=ConnectionPhase.lease)
    now = 61.0
    diagnostics.report(ExecutorHttpError(403, "denied", "private"), phase=ConnectionPhase.lease)
    return dict(results=results, logs=logs)


def test_unit22_mac_http(tmp_path: Path, record_property: Any) -> None:
    started = time.monotonic()
    ca, key = tmp_path / "ca.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=synthetic-unit22",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(ca),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    homes = create_synthetic_homes(tmp_path)
    content = ("immutable λ snapshot\n" * 20000).encode()
    source = tmp_path / "source"
    source.write_bytes(content)
    roots, replies = fixtures(content)
    traces: list[list[Any]] = [[], []]
    phase = 0
    counts: dict[str, int] = {}
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(ca, key)

    async def exercise() -> None:
        nonlocal phase
        release_poll = asyncio.Event()

        async def handle(request: web.Request) -> web.Response:
            if request.path == "/early-response":
                return web.Response(text="early")
            if request.path == "/hold-poll":
                await release_poll.wait()
                return web.Response(text="poll complete")
            if request.path == "/release-poll":
                release_poll.set()
                return web.Response(text="lease complete")
            if request.path == "/stall":
                await asyncio.sleep(0.1)
                return web.Response(text="late")
            if request.path == "/custody":
                try:
                    await request.read()
                except ConnectionError:
                    pass
                return web.Response(text="done")
            body = await request.read()
            headers = {
                name: request.headers.get(name)
                for name in (
                    "X-Panels-Executor-ID",
                    "X-Panels-Executor-Incarnation",
                    "X-Panels-Executor-Protocol",
                    "Accept-Encoding",
                    "Content-Type",
                    "X-Panels-File-Upload",
                )
            }
            traces[phase].append(
                [
                    request.method,
                    request.path_qs,
                    headers,
                    hashlib.sha256(body).hexdigest(),
                    len(body),
                ]
            )
            path = request.path
            index = counts.get(path, 0)
            counts[path] = index + 1
            status = 200
            if path == "/api/executor/lease":
                if index == 0:
                    reply = encoded("LeaseReply", typed("LeaseReply", replies["lease"]))
                elif index == 1:
                    reply, status = (
                        encoded("LeaseRefused", typed("LeaseRefused", replies["refused"])),
                        409,
                    )
                elif index == 2:
                    reply, status = (
                        '{"error":{"code":"INVALID!","message":"' + "λ" * 505 + '"}}',
                        409,
                    )
                else:
                    return web.Response(
                        status=302, headers={"Location": "https://127.0.0.1:9/live"}
                    )
            elif path == "/api/executor/commands":
                if index:
                    return web.Response(body=b"x" * (4 * 1024 * 1024 + 1))
                reply = encoded("MailboxPollReply", typed("MailboxPollReply", replies["poll"]))
            elif path.endswith("/outcomes"):
                p.decode_bounded(body, p.CommandOutcomeReport, limit=4 * 1024 * 1024)
                reply = encoded(
                    "CommandOutcomeReply", typed("CommandOutcomeReply", replies["report"])
                )
            elif path.endswith("/items"):
                p.decode_bounded(body, p.ItemBatch, limit=4 * 1024 * 1024)
                reply = encoded("ItemBatchReply", typed("ItemBatchReply", replies["items"]))
            elif path == "/api/executor/reads":
                p.decode_bounded(body, p.LiveReadRequest)
                reply = encoded("JsonObject", replies["read"])
            elif path.endswith("/live") or path.startswith("/api/executor/uploads/"):
                return web.Response(status=204)
            elif "/files/" in path:
                digest = hashlib.sha256(content).hexdigest() if path.endswith("/file") else "0" * 64
                return web.Response(body=content, headers={"X-Panels-File-Sha256": digest})
            else:
                raise AssertionError(path)
            return web.Response(body=reply.encode(), status=status, content_type="application/json")

        app = web.Application(client_max_size=5 * 1024 * 1024)
        app.router.add_route("*", "/{tail:.*}", handle)
        runner = web.AppRunner(app, shutdown_timeout=1)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
        await site.start()
        assert site._server is not None
        port = cast(asyncio.Server, site._server).sockets[0].getsockname()[1]
        origin = f"https://127.0.0.1:{port}"
        try:
            expected = await reference(origin, ca, roots, source, tmp_path / "python-target")
            counts.clear()
            phase = 1
            spec = tmp_path / "spec.json"
            spec.write_text(
                json.dumps(
                    dict(
                        origin=origin,
                        owned_origin=origin,
                        ca=str(ca),
                        source=str(source),
                        target=str(tmp_path / "node-target"),
                        identity=IDENTITY,
                        roots=roots,
                    )
                )
            )
            environment = {
                name: value for name, value in os.environ.items() if not name.startswith("PLAN_")
            }
            environment.update(homes)
            environment.update(
                PLAN_SERVER_URL="http://127.0.0.1:9",
                PLAN_TEST_MODE="1",
                PLAN_EXECUTOR_ROOT=str(tmp_path / "executor"),
                HTTPS_PROXY="http://127.0.0.1:9",
                HTTP_PROXY="http://127.0.0.1:9",
            )
            child = await asyncio.create_subprocess_exec(
                "node",
                "--input-type=module",
                "-e",
                "import {readFileSync} from 'node:fs'; const {run}=await import(process.argv[1]); "
                "const input=JSON.parse(readFileSync(process.argv[2],'utf8')); "
                "console.log(JSON.stringify(await run(input)));",
                (ROOT / "server/executor/mac/testing/http_parity.ts").as_uri(),
                str(spec),
                cwd=ROOT,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=20)
            except BaseException:
                if child.returncode is None:
                    child.kill()
                await child.wait()
                raise
            assert child.returncode == 0, stderr.decode()
            actual = json.loads(stdout)
            assert actual.pop("custody") is True
            assert actual.pop("destination_guard") is True
            assert actual == expected
            assert traces[0] == traces[1]
            assert (tmp_path / "node-target").read_bytes() == content
            assert len(traces[0]) == 14
        finally:
            await runner.cleanup()

    asyncio.run(exercise())
    record_property("connected_seconds", round(time.monotonic() - started, 3))
