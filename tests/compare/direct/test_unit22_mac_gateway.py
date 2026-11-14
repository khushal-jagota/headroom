"""Gateway parity on populated real spools; one private Unix listener, no TCP or providers.

The CLI proof cannot prove source ownership, child-scoped receipt lookup, immutable
file capture, or catalog-before-quiescence ordering. This flow crosses those gaps
with current Python, then measures TS actual cleanup beyond a shared deadline.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from tests.compare.direct.mac_executor_fixture import report_direct, run_node
from tests.support.executor_presence import mac_identity
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.execution_identity import ChildReference, TurnToken
from planner.executor import ipc_client
from planner.executor import protocol as p
from planner.executor.mac_agent_gateway import AgentGateway
from planner.executor.mac_http import ExecutorHttpError
from planner.executor.mac_outbox import MacOutbox
from planner.executor.mac_presence import MacPresence


def inputs() -> dict[str, Any]:
    return dict(
        identity=p.as_object(mac_identity("inc")),
        child=dict(executor_incarnation="inc", child_id="child", conversation_id="conversation"),
        turn=dict(conversation_id="conversation", turn_number=123456799),
        high=123456799,
    )


async def reference(root: Path, patch: pytest.MonkeyPatch) -> dict[str, Any]:
    values = inputs()
    child = SimpleNamespace(
        reference=p.type_adapter(ChildReference).validate_python(values["child"]),
        ticket_id="ticket",
        active_turn=p.type_adapter(TurnToken).validate_python(values["turn"]),
    )
    other = SimpleNamespace(reference=ChildReference("inc", "other", "conversation"))
    calls: list[Any] = []
    gate = asyncio.Event()
    sources: list[asyncio.Task[Any]] = []

    class Runner:
        stopped = False

        @property
        def admission_stopped(self) -> bool:
            return self.stopped

        @admission_stopped.setter
        def admission_stopped(self, value: bool) -> None:
            self.stopped = value
            calls.append(["seal"])

        def authenticate(self, identity: str, capability: str) -> Any:
            if identity not in ("child", "other") or capability != "capability":
                raise PermissionError("unknown executor child capability")
            return child if identity == "child" else other

        def source_work(self, reference: Any, work: Any) -> asyncio.Task[Any]:
            async def owned() -> Any:
                await gate.wait()
                return await work()

            task = asyncio.create_task(owned())
            sources.append(task)
            return task

        async def quiesce(self) -> p.Quiescence:
            calls.append(["quiesce"])
            return p.Quiescence(True, 0, 0, 0, 0, True)

    class Client:
        async def read(self, read: Any) -> Any:
            calls.append(["read", p.as_object(read)])
            if read.path == "/ok":
                return {"text": "fresh VPS result", "high": values["high"]}
            if read.path == "/timeout":
                raise TimeoutError("timeout")
            raise ExecutorHttpError(int(read.path[1:]), "refused", "scripted read refusal")

    runner = Runner()
    catalog_entered, catalog_release = asyncio.Event(), asyncio.Event()

    async def catalogs() -> None:
        assert runner.admission_stopped
        calls.append(["catalog"])
        catalog_entered.set()
        await catalog_release.wait()

    spool = MacOutbox(root / "spool")
    gateway = AgentGateway(
        runner=cast(Any, runner),
        outbox=spool,
        presence=MacPresence(mac_identity("inc")),
        client=cast(Any, Client()),
        request_exit=lambda: calls.append(["exit"]),
        settle_catalogs=catalogs,
    )

    def make(identity: str, operation: str, payload: dict[str, Any], **extra: Any) -> p.IpcRequest:
        return p.type_adapter(p.IpcRequest).validate_python(
            dict(
                request_id=identity,
                operation=operation,
                payload=payload,
                child_id="child",
                capability="capability",
                response_deadline=None,
            )
            | extra
        )

    replies: list[Any] = []
    artifact = root / "artifacts/tickets/ticket/file.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("original artifact")
    try:
        patch.setenv("PLAN_EXECUTOR_ROOT", str(root))
        async with gateway.serve(root / "run/executor.sock"):
            health = p.as_object(
                await asyncio.to_thread(ipc_client.request, p.IpcOperation.health, {})
            )["result"]
            assert isinstance(health, dict)
            del health["process_id"]
            replies.append(health)
            replies.append(
                p.as_object(
                    await gateway.handle(
                        make("bad", "retry", {"submission_id": "first"}, capability="wrong")
                    )
                )
            )
            for route in ("/ok", "/400", "/403", "/500", "/timeout"):
                replies.append(
                    p.as_object(
                        await gateway.handle(
                            make(route, "read", {"path": route, "params": {"q": values["high"]}})
                        )
                    )
                )
            replies.append(
                p.as_object(
                    await gateway.handle(
                        make(
                            "first",
                            "write",
                            {"operation": "ticket.edit", "arguments": {"text": "first"}},
                            response_deadline=0,
                        )
                    )
                )
            )
            replies.append(
                p.as_object(
                    await gateway.handle(make("preparing", "retry", {"submission_id": "first"}))
                )
            )
            replies.append(
                p.as_object(
                    await gateway.handle(
                        make(
                            "foreign-preparing",
                            "retry",
                            {"submission_id": "first"},
                            child_id="other",
                        )
                    )
                )
            )
            child.active_turn = None
            gate.set()
            await asyncio.gather(*sources)
            artifact.write_text("changed artifact")
            replies.append(
                p.as_object(
                    await gateway.handle(make("queued", "retry", {"submission_id": "first"}))
                )
            )
            record = spool.lookup("first")
            assert record is not None
            envelope = p.as_object(record.envelope)
            envelope["sender_id"] = "sender"
            receipt = p.AcceptanceReceipt(
                spool.sender_id,
                "first",
                p.ReceiptDisposition.accepted,
                {"id": "saved", "high": values["high"]},
                1700000000123,
            )
            waiter = asyncio.create_task(spool.wait_confirmed("first", 1))
            await asyncio.to_thread(spool.acknowledge, receipt)
            confirmed = await waiter
            assert confirmed is not None and confirmed.submission_id == "first"
            for identity, submission, extra in (
                ("accepted", "first", {}),
                ("foreign-stored", "first", {"child_id": "other"}),
                ("unknown", "absent", {}),
            ):
                replies.append(
                    p.as_object(
                        await gateway.handle(
                            make(identity, "retry", {"submission_id": submission}, **extra)
                        )
                    )
                )
            quiescing = asyncio.create_task(
                asyncio.to_thread(
                    ipc_client.request, p.IpcOperation.quiesce, {"exit_when_ready": True}
                )
            )
            await catalog_entered.wait()
            sealed = p.as_object(
                await gateway.handle(
                    make("sealed", "write", {"operation": "ticket.edit", "arguments": {}})
                )
            )
            catalog_release.set()
            quiesced = p.as_object(await quiescing)
            quiesced["request_id"] = "quiesce"
            assert isinstance(quiesced["result"], dict)
            del quiesced["result"]["process_id"]
            replies.append(quiesced)
            replies.append(sealed)
        return dict(replies=replies, envelope=envelope, calls=calls)
    finally:
        await asyncio.gather(*sources)
        spool.close()


def test_mac_gateway_populated_source_and_receipt_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    # Keep the socket below the Unix path length on Darwin and Linux.
    with tempfile.TemporaryDirectory(prefix="gateway-") as temporary:
        root = Path(temporary).resolve()
        with synthetic_home_environment(homes), monkeypatch.context() as patch:
            expected = asyncio.run(asyncio.wait_for(reference(root / "py", patch), 15))
        target = root / "ts"
        target.mkdir()
        (target / "input.json").write_text(json.dumps(inputs()))
        observed = run_node(
            "server/executor/mac/testing/gateway_parity.ts", target, homes, timeout=20
        )
        (target / "expected.json").write_text(json.dumps(expected, indent=2))
        (target / "observed.json").write_text(json.dumps(observed, indent=2))
        assert observed["parity"] == expected
        assert all(observed["lifecycle"].values())
        report_direct(
            request,
            "unit22-mac-gateway.json",
            observed,
            started,
            "private Unix listener and populated spool; actual publication joins beyond deadline",
        )
