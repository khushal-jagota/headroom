"""Regression tests for the Python Claude SDK's bounded NDJSON message reader."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any, cast

import pytest
from anyio.abc import Process
from anyio.streams.text import TextReceiveStream
from claude_agent_sdk import ClaudeAgentOptions, ToolResultBlock, UserMessage
from claude_agent_sdk._errors import CLIJSONDecodeError
from claude_agent_sdk._internal.message_parser import parse_message
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

from planner.conversation.backends.claude_agent_sdk import CLAUDE_SDK_MAX_BUFFER_SIZE


class _ChunkStream:
    def __init__(self, chunks: Iterable[str]) -> None:
        self._chunks = iter(chunks)

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._chunks)
        except StopIteration as end:
            raise StopAsyncIteration from end


class _ExitedProcess:
    async def wait(self) -> int:
        return 0


async def _read_messages(line: str) -> list[dict[str, Any]]:
    transport = SubprocessCLITransport(
        prompt="",
        options=ClaudeAgentOptions(max_buffer_size=CLAUDE_SDK_MAX_BUFFER_SIZE),
    )
    transport._process = cast(Process, _ExitedProcess())
    chunks = (line[start : start + 65536] for start in range(0, len(line), 65536))
    transport._stdout_stream = cast(TextReceiveStream, _ChunkStream(chunks))
    return [message async for message in transport.read_messages()]


def _tool_result_line(content: str) -> str:
    return (
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": content,
                            "is_error": False,
                        }
                    ],
                },
                "parent_tool_use_id": None,
            }
        )
        + "\n"
    )


def test_a_tool_result_just_over_one_megabyte_is_parsed() -> None:
    async def exercise() -> None:
        content = "x" * (1024 * 1024 + 1)

        messages = await _read_messages(_tool_result_line(content))

        parsed = parse_message(messages[0])
        assert isinstance(parsed, UserMessage)
        assert isinstance(parsed.content, list)
        assert isinstance(parsed.content[0], ToolResultBlock)
        assert parsed.content[0].content == content

    asyncio.run(exercise())


def test_a_tool_result_over_the_new_limit_fails_cleanly() -> None:
    async def exercise() -> None:
        content = "x" * (CLAUDE_SDK_MAX_BUFFER_SIZE + 1)

        with pytest.raises(CLIJSONDecodeError) as failure:
            await _read_messages(_tool_result_line(content))

        assert (
            f"JSON message exceeded maximum buffer size of {CLAUDE_SDK_MAX_BUFFER_SIZE} bytes"
            in str(failure.value)
        )

    asyncio.run(exercise())
