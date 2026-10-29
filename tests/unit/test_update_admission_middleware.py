"""ASGI cancellation and admission boundaries without a browser, server, or database."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import h11
import pytest
from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn._types import ASGI3Application
from uvicorn.protocols.http.flow_control import FlowControl
from uvicorn.protocols.http.h11_impl import RequestResponseCycle

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.config import Config, load_config
from planner.environments.update_admission_middleware import UpdateAdmissionMiddleware
from planner.navigation.api import DeadlineStreamingResponse
from planner.navigation.delivery_authority import DeliveryAccess, DeliveryAdmission

SHA = "a" * 40
STALE_SHA = "b" * 40
STATUS_PATHS = ("/api/health", "/api/meta", "/api/deployment-status", "/api/updates")
WAIT_PAYLOAD = {
    "detail": (
        "Normal work waits for the deployment runner. "
        "Recovery requires the owner if progress expires."
    )
}
STALE_PAYLOAD = {
    "error": {
        "code": "validation",
        "message": "Refresh page before making changes.",
        "detail": {},
    }
}


class CountedAdmission(WorkspaceAdmission):
    def __init__(self) -> None:
        super().__init__()
        self.entries = 0
        self.exits = 0

    @contextmanager
    def operation(self) -> Iterator[bool]:
        self.entries += 1
        try:
            with super().operation() as admitted:
                yield admitted
        finally:
            self.exits += 1


@dataclass
class Harness:
    owner: FastAPI
    config: Config
    admission: CountedAdmission

    def wrap(self, downstream: ASGIApp) -> UpdateAdmissionMiddleware:
        return UpdateAdmissionMiddleware(downstream, config=self.config)

    def scope(self, method: str = "GET", path: str = "/api/projects") -> Scope:
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "http",
            "method": method,
            "path": path,
            "root_path": "",
            "query_string": b"",
            "headers": [],
            "app": self.owner,
        }


@pytest.fixture
def harness() -> Harness:
    owner = FastAPI()
    owner.state.update_startup_pending = False
    owner.state.update_admission_closed = False
    admission = CountedAdmission()
    owner.state.conversation_system = SimpleNamespace(workspace_admission=admission)
    return Harness(owner, load_config(path=None, env={"PLAN_APP_SHA": SHA}), admission)


async def idle_receive() -> Message:
    await asyncio.Event().wait()
    raise AssertionError("unreachable")


async def invoke(app: ASGIApp, scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, idle_receive, send)
    return sent


def payload(sent: list[Message]) -> object:
    return json.loads(b"".join(m.get("body", b"") for m in sent))


async def answer(scope: Scope, receive: Receive, send: Send) -> None:
    await JSONResponse({"forwarded": True})(scope, receive, send)


@pytest.mark.parametrize(
    "method", ("GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE", "TRACE")
)
def test_exact_safe_methods_sha_precedence_and_write_lease(harness: Harness, method: str) -> None:
    async def run() -> None:
        scope = harness.scope(method)
        scope["headers"] = [(b"x-panels-app-sha", STALE_SHA.encode())]
        middleware = harness.wrap(answer)
        safe = method in {"GET", "HEAD", "OPTIONS"}
        # A stale write wins over both closed admission gates and acquires no lease.
        harness.owner.state.update_startup_pending = not safe
        harness.owner.state.update_admission_closed = not safe
        sent = await invoke(middleware, scope)
        assert sent[0]["status"] == (200 if safe else 409)
        assert payload(sent) == ({"forwarded": True} if safe else STALE_PAYLOAD)
        assert harness.admission.entries == harness.admission.exits == 0

        harness.owner.state.update_startup_pending = False
        harness.owner.state.update_admission_closed = False
        scope["headers"] = [(b"x-panels-app-sha", SHA.encode())]

        async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
            assert harness.admission.active is not safe
            await answer(scope, receive, send)

        assert (await invoke(harness.wrap(endpoint), scope))[0]["status"] == 200
        assert harness.admission.entries == harness.admission.exits == (0 if safe else 1)
        assert not harness.admission.active

    asyncio.run(run())


@pytest.mark.parametrize("closed_gate", ("update_startup_pending", "update_admission_closed"))
def test_exact_status_and_static_exceptions_to_closed_gate(
    harness: Harness, closed_gate: str
) -> None:
    async def run() -> None:
        setattr(harness.owner.state, closed_gate, True)
        cases = [(method, path, True) for path in STATUS_PATHS for method in ("GET", "POST")]
        cases += [
            ("GET", "/assets/app.css", True),
            ("HEAD", "/assets/app.css", True),
            ("GET", "/api", True),
            ("OPTIONS", "/assets/app.css", False),
            ("POST", "/assets/app.css", False),
            ("GET", "/api/projects", False),
            ("HEAD", "/api/projects", False),
            ("OPTIONS", "/api/projects", False),
            ("GET", "/api/health/", False),
            ("GET", "/api/updates/claim", False),
            ("POST", "/api/updates/claim", False),
        ]
        for method, path, allowed in cases:
            sent = await invoke(harness.wrap(answer), harness.scope(method, path))
            assert sent[0]["status"] == (200 if allowed else 503), (method, path)
            assert payload(sent) == ({"forwarded": True} if allowed else WAIT_PAYLOAD)
        assert harness.admission.entries == harness.admission.exits == 0

    asyncio.run(run())


def test_update_prefix_excludes_lease_but_not_sha_and_absent_system_forwards(
    harness: Harness,
) -> None:
    async def run() -> None:
        for path in (*STATUS_PATHS, "/api/updates/claim", "/api/updates-extra"):
            scope = harness.scope("POST", path)
            assert (await invoke(harness.wrap(answer), scope))[0]["status"] == 200
            scope["headers"] = [(b"x-panels-app-sha", STALE_SHA.encode())]
            sent = await invoke(harness.wrap(answer), scope)
            assert sent[0]["status"] == 409
            assert payload(sent) == STALE_PAYLOAD
        assert harness.admission.entries == harness.admission.exits == 0
        harness.owner.state.conversation_system = None
        assert (await invoke(harness.wrap(answer), harness.scope("POST")))[0]["status"] == 200
        assert harness.admission.entries == 0

    asyncio.run(run())


def test_refused_write_keeps_existing_payload_and_never_calls_endpoint(harness: Harness) -> None:
    async def run() -> None:
        assert harness.admission.claim(lambda: None, idle=lambda: True, require_idle=False)

        async def unexpected(scope: Scope, receive: Receive, send: Send) -> None:
            raise AssertionError("a refused write reached its endpoint")

        sent = await invoke(harness.wrap(unexpected), harness.scope("POST"))
        assert sent[0]["status"] == 503
        assert payload(sent) == {"detail": "Owner update in progress."}
        assert harness.admission.entries == harness.admission.exits == 1
        assert not harness.admission.active

    asyncio.run(run())


def test_write_lease_ends_once_at_headers_before_upstream_send_or_body(harness: Harness) -> None:
    async def run() -> None:
        entered, allow_headers, upstream_headers, allow_upstream = (
            asyncio.Event() for _ in range(4)
        )

        async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
            assert harness.admission.active
            entered.set()
            await allow_headers.wait()
            assert harness.admission.active
            await send({"type": "http.response.start", "status": 200, "headers": []})
            assert not harness.admission.active
            await send({"type": "http.response.body", "body": b"done"})

        async def send(message: Message) -> None:
            assert not harness.admission.active
            assert harness.admission.exits == 1
            if message["type"] == "http.response.start":
                upstream_headers.set()
                await allow_upstream.wait()

        task = asyncio.create_task(
            harness.wrap(endpoint)(harness.scope("POST"), idle_receive, send)
        )
        await entered.wait()
        assert harness.admission.active and harness.admission.exits == 0
        allow_headers.set()
        await upstream_headers.wait()
        assert not harness.admission.active and not task.done()
        allow_upstream.set()
        await task
        assert harness.admission.entries == harness.admission.exits == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "ending", ("error-before", "error-after", "disconnect", "cancel", "send-error")
)
def test_write_lease_releases_once_on_error_disconnect_or_cancellation(
    harness: Harness, ending: str
) -> None:
    async def run() -> None:
        failure = ValueError("application failure")

        async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
            assert harness.admission.active and harness.admission.exits == 0
            if ending == "disconnect":
                assert (await receive())["type"] == "http.disconnect"
                return
            if ending == "cancel":
                raise asyncio.CancelledError
            if ending == "error-before":
                raise failure
            await send({"type": "http.response.start", "status": 200, "headers": []})
            assert not harness.admission.active and harness.admission.exits == 1
            raise failure

        async def receive() -> Message:
            return {"type": "http.disconnect"}

        async def send(message: Message) -> None:
            assert not harness.admission.active and harness.admission.exits == 1
            if ending == "send-error":
                raise failure

        if ending == "disconnect":
            await harness.wrap(endpoint)(harness.scope("POST"), receive, send)
        else:
            expected = asyncio.CancelledError if ending == "cancel" else ValueError
            with pytest.raises(expected):
                await harness.wrap(endpoint)(harness.scope("POST"), receive, send)
        assert not harness.admission.active
        assert harness.admission.entries == harness.admission.exits == 1

    asyncio.run(run())


@pytest.mark.parametrize("kind", ("lifespan", "websocket"))
def test_non_http_scope_and_callables_pass_through_unchanged(harness: Harness, kind: str) -> None:
    async def run() -> None:
        scope: Scope = {"type": kind}
        harness.owner.state.update_startup_pending = True

        async def send(message: Message) -> None:
            raise AssertionError("unexpected send")

        async def endpoint(actual: Scope, receive: Receive, actual_send: Send) -> None:
            assert actual is scope
            assert receive is idle_receive
            assert actual_send is send

        await harness.wrap(endpoint)(scope, idle_receive, send)
        assert harness.admission.entries == 0

    asyncio.run(run())


def test_navigation_disconnect_before_headers_finishes_without_synthetic_response(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        admission = DeliveryAdmission()

        def validate(read: DeliveryAccess) -> None:
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2), "the test did not release its validation thread"

        monkeypatch.setattr(admission, "validate", validate)

        async def body() -> AsyncIterator[bytes]:
            yield b": heartbeat\n\n"

        async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
            await DeadlineStreamingResponse(body(), send_timeout=3, admission=admission)(
                scope, receive, send
            )

        async def receive() -> Message:
            await entered.wait()
            return {"type": "http.disconnect"}

        sent: list[Message] = []

        async def send(message: Message) -> None:
            sent.append(message)

        try:
            await asyncio.wait_for(
                harness.wrap(endpoint)(
                    harness.scope(path="/api/navigation/view/events"), receive, send
                ),
                timeout=1,
            )
        finally:
            release.set()
        assert sent == []

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", (ValueError("application failure"), RuntimeError("No response returned."))
)
def test_unexpected_errors_propagate_even_with_the_old_error_text(
    harness: Harness, failure: Exception
) -> None:
    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        raise failure

    async def run() -> None:
        with pytest.raises(type(failure)) as raised:
            await invoke(harness.wrap(endpoint), harness.scope())
        assert raised.value is failure

    asyncio.run(run())


class MemoryTransport(asyncio.Transport):
    def __init__(self) -> None:
        self.writes: list[bytes] = []

    def write(self, data: Any) -> None:
        self.writes.append(bytes(data))

    def close(self) -> None:
        pass


def test_connected_header_timeout_still_logs_and_returns_500_at_uvicorn_boundary(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    async def run() -> None:
        for disconnected in (False, True):
            caplog.clear()
            transport = MemoryTransport()
            flow = FlowControl(transport)
            flow.pause_writing()
            connection = h11.Connection(h11.SERVER)
            connection.receive_data(b"GET / HTTP/1.1\r\nHost: fixture\r\n\r\n")
            connection.next_event()
            connection.next_event()
            cycle = RequestResponseCycle(
                scope=cast(Any, harness.scope(path="/api/navigation/view/events")),
                conn=connection,
                transport=transport,
                flow=flow,
                logger=logging.getLogger("uvicorn.error"),
                access_logger=logging.getLogger("uvicorn.access"),
                access_log=False,
                default_headers=[],
                message_event=asyncio.Event(),
                on_response=lambda: None,
            )
            cycle.disconnected = disconnected

            async def body() -> AsyncIterator[bytes]:
                yield b": heartbeat\n\n"

            async def endpoint(
                scope: Scope, receive: Receive, send: Send, flow: FlowControl = flow
            ) -> None:
                try:
                    await DeadlineStreamingResponse(body(), send_timeout=0.01)(scope, receive, send)
                finally:
                    flow.resume_writing()

            await cycle.run_asgi(cast(ASGI3Application, harness.wrap(endpoint)))
            if disconnected:
                assert caplog.records == []
                assert transport.writes == []
                assert not cycle.response_started
            else:
                assert [record.getMessage() for record in caplog.records] == [
                    "ASGI callable returned without starting response."
                ]
                assert cycle.response_complete
                assert b"500 Internal Server Error" in b"".join(transport.writes)

    asyncio.run(run())
