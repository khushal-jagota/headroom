"""Stream incompleteness and reconnect differences must not become false equality."""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from tests.compare.runner import stream_answers


def test_stream_oracle_rejects_false_equality(monkeypatch: pytest.MonkeyPatch) -> None:
    class IncompleteStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b': heartbeat\n\ndata: {"partial":true}\n'
            await asyncio.sleep(1)

    def response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/complete":
            event_id = request.url.host
            retry = 100 if event_id == "left" else 200
            content = f'id: {event_id}\nretry: {retry}\ndata: {{"same":true}}\n\n'
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content,
            )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=IncompleteStream(),
        )

    client = httpx.AsyncClient
    transport = httpx.MockTransport(response)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: client(transport=transport, **kwargs),
    )
    call = {
        "kind": "stream", "path": "/events", "status": 200,
        "frames": 1, "deadline_seconds": 0.02,
    }
    with pytest.raises(AssertionError, match="required complete frames"):
        asyncio.run(stream_answers(["http://left", "http://right"], [call, call]))

    complete = {**call, "path": "/complete", "deadline_seconds": 1}
    answers = asyncio.run(stream_answers(
        ["http://left", "http://right"], [complete, complete],
    ))
    assert answers[0]["body"][0]["id"] != answers[1]["body"][0]["id"]
    assert answers[0]["body"][0]["retry"] != answers[1]["body"][0]["retry"]
