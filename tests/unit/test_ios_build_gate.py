"""Native rejection must happen before route side effects or streaming starts."""

import asyncio
import json

import pytest
from starlette.types import Message, Receive, Scope, Send

from planner.ios_compatibility.middleware import IOSBuildGateMiddleware


async def receive() -> Message:
    return {"type": "http.request"}


@pytest.mark.parametrize("scope_type", ["http", "websocket"])
@pytest.mark.parametrize("value", [b"0", b"", b"one", b"-1", b"1" * 5000])
def test_incompatible_native_never_reaches_endpoint(scope_type: str, value: bytes) -> None:
    messages: list[Message] = []

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        pytest.fail("rejected native request reached endpoint")

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": scope_type,
        "headers": [(b"x-panels-ios-build", value)],
        "extensions": {"websocket.http.response": {}},
    }
    asyncio.run(IOSBuildGateMiddleware(endpoint)(scope, receive, send))
    assert messages[0]["status"] == 426
    assert json.loads(messages[1]["body"])["error"]["code"] == "ios_update_required"


@pytest.mark.parametrize(
    "headers", [[], [(b"x-panels-ios-build", b"1")], [(b"x-panels-ios-build", b"2")]]
)
def test_supported_native_and_other_clients_reach_endpoint(
    headers: list[tuple[bytes, bytes]],
) -> None:
    reached: list[Scope] = []

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        reached.append(scope)

    async def send(message: Message) -> None:
        pytest.fail("middleware sent a response for an accepted client")

    asyncio.run(
        IOSBuildGateMiddleware(endpoint)({"type": "http", "headers": headers}, receive, send)
    )
    assert len(reached) == 1


def test_duplicate_build_headers_and_websocket_without_denial_extension() -> None:
    messages: list[Message] = []

    async def endpoint(scope: Scope, receive: Receive, send: Send) -> None:
        pytest.fail("duplicate build headers reached endpoint")

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "websocket",
        "headers": [(b"x-panels-ios-build", b"1"), (b"X-Panels-iOS-Build", b"2")],
    }
    asyncio.run(IOSBuildGateMiddleware(endpoint)(scope, receive, send))
    assert messages == [
        {"type": "websocket.close", "code": 4406, "reason": "Update Panels from TestFlight"}
    ]
