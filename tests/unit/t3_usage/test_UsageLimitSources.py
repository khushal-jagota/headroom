"""Missing upstream coverage: empty source configuration must cause no transport action."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from planner.conversation.usage.cliproxyApi import makeCliproxyApi
from planner.conversation.usage.UsageLimitSources import UsageLimitSources


def test_empty_source_configuration_never_uses_transport() -> None:
    class Settings:
        async def getSettings(self) -> dict[str, Any]:
            return {"usageLimitSources": {}}

        async def streamChanges(self) -> AsyncIterator[dict[str, Any]]:
            yield {"usageLimitSources": {}}

    async def run() -> None:
        calls: list[dict[str, Any]] = []

        async def transport(request: dict[str, Any]) -> dict[str, Any]:
            calls.append(request)
            raise AssertionError("No source means no transport request")

        async def backgroundPolicy(_: dict[str, str]) -> bool:
            return False

        source = UsageLimitSources(
            makeCliproxyApi(transport), Settings(), backgroundPolicy, lambda _: 60, 60
        )
        await source.refresh()
        assert source.current() == []
        assert calls == []

    asyncio.run(run())
