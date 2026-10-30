"""Response framing and failure propagation without a transport fixture."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from starlette.types import Message

from planner.navigation.api import DeadlineStreamingResponse
from planner.navigation.delivery_authority import DeliveryAdmission, DeliveryUnavailable


@pytest.mark.parametrize("failure", ["healthy", "before_headers", "after_headers"])
def test_publication_loss_and_completion(failure: str) -> None:
    async def run() -> None:
        messages: list[Message] = []
        closed = False
        admission = DeliveryAdmission()
        if failure == "before_headers":
            admission.configure(None)

        async def body() -> AsyncIterator[bytes]:
            nonlocal closed
            try:
                yield b"allowed"
                if failure == "after_headers":
                    admission.configure(None)
                    yield b"forbidden"
            finally:
                closed = True

        async def send(message: Message) -> None:
            messages.append(message)

        response = DeadlineStreamingResponse(body(), send_timeout=0.1, admission=admission)
        await response.stream_response(send)
        if failure == "before_headers":
            assert messages[0]["status"] == 503
            assert (b"cache-control", b"no-store") in messages[0]["headers"]
            assert messages[1]["body"] == b'{"detail":"navigation publication is not ready"}'
            assert not messages[1].get("more_body", False)
        else:
            assert messages[0]["status"] == 200
            assert messages[1] == {
                "type": "http.response.body",
                "body": b"allowed",
                "more_body": True,
            }
            assert messages[2:] == [{"type": "http.response.body", "body": b"", "more_body": False}]
            assert closed

    asyncio.run(run())


@pytest.mark.parametrize("terminal", [False, True])
def test_send_timeout_propagates_and_closes_iterator(terminal: bool) -> None:
    async def run() -> None:
        closed = False
        cancelled = False

        async def body() -> AsyncIterator[bytes]:
            nonlocal closed
            try:
                yield b"allowed"
                raise DeliveryUnavailable()
            finally:
                closed = True

        async def send(message: Message) -> None:
            nonlocal cancelled
            if (
                message["type"] == "http.response.body"
                and bool(message.get("more_body", False)) != terminal
            ):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled = True

        response = DeadlineStreamingResponse(body(), send_timeout=0.01)
        with pytest.raises(TimeoutError):
            await response.stream_response(send)
        assert closed
        assert cancelled

    asyncio.run(run())
