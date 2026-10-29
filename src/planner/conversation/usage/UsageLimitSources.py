"""Mechanical T3 UsageLimitSources.ts translation (75e4ceb).

Effect services, scoped fibers, streams and Ref become injected dependencies,
owned asyncio tasks, subscriber queues and ordinary in-process state.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

from .cliproxyApi import CliproxyApi
from .contracts.providerUsageLimits import UsageLimitSourceError


class SettingsService(Protocol):
    async def getSettings(self) -> dict[str, Any]: ...

    def streamChanges(self) -> AsyncIterator[dict[str, Any]]: ...


def sourceLabel(id: str, config: dict[str, Any]) -> str:
    if config.get("label"):
        return str(config["label"])
    try:
        url = urlparse(config["url"])
        if not url.scheme or (url.scheme in ("http", "https") and not url.hostname):
            raise ValueError("Invalid URL")
        host = str(url.hostname or "")
        if ":" in host:
            host = f"[{host}]"
        if url.port is not None and (url.scheme, url.port) not in (("http", 80), ("https", 443)):
            host = f"{host}:{url.port}"
        return host
    except ValueError:
        return id


class UsageLimitSources:
    def __init__(
        self,
        api: CliproxyApi,
        settingsService: SettingsService,
        backgroundPolicy: Callable[[dict[str, str]], Awaitable[bool]],
        resolveInterval: Callable[[dict[str, Any]], float],
        defaultInterval: float,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.api = api
        self.settingsService = settingsService
        self.backgroundPolicy = backgroundPolicy
        self.resolveInterval = resolveInterval
        self.defaultInterval = defaultInterval
        self.now = now
        self.stateRef: list[dict[str, Any]] = []
        self.changes: set[asyncio.Queue[list[dict[str, Any]] | None]] = set()
        self.refreshLock = asyncio.Lock()
        self._tasks: list[asyncio.Task[None]] = []

    async def readSource(self, id: str, config: dict[str, Any]) -> dict[str, Any]:
        checkedAt = (
            self.now().astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        base = {
            "id": id,
            "kind": config["kind"],
            "label": sourceLabel(id, config),
            "checkedAt": checkedAt,
        }
        if len(config["managementKey"]) == 0:
            return {**base, "accounts": [], "error": "No management key configured."}
        try:
            accounts = await self.api.readAccounts(config)
        except UsageLimitSourceError as error:
            return {**base, "accounts": [], "error": error.detail}
        return {**base, "accounts": accounts}

    def publish(self, next: list[dict[str, Any]]) -> None:
        if self.stateRef != next:
            self.stateRef = next
            for queue in self.changes:
                queue.put_nowait(next)

    async def refresh(self) -> None:
        async with self.refreshLock:
            try:
                try:
                    settings = await self.settingsService.getSettings()
                except Exception:
                    settings = {}
                entries = [
                    (id, config)
                    for id, config in settings.get("usageLimitSources", {}).items()
                    if config["enabled"]
                ]
                semaphore = asyncio.Semaphore(4)

                async def read(id: str, config: dict[str, Any]) -> dict[str, Any]:
                    async with semaphore:
                        return await self.readSource(id, config)

                self.publish(
                    list(await asyncio.gather(*(read(id, config) for id, config in entries)))
                )
            except Exception:
                # Effect.ignoreCause: a source refresh does not fail its callers.
                return

    async def consumeResetCredit(self, input: dict[str, str]) -> dict[str, Any]:
        async with self.refreshLock:
            try:
                settings = await self.settingsService.getSettings()
            except Exception as error:
                raise UsageLimitSourceError("Could not read hub settings.") from error
            config = settings["usageLimitSources"].get(input["sourceId"])
            if not config or not config["enabled"] or not config["managementKey"]:
                raise UsageLimitSourceError("The usage limit source is missing or disabled.")
            result = await self.api.consume(config, input["accountId"], input["creditId"])
            snapshot = await self.readSource(input["sourceId"], config)
            self.publish(
                [
                    snapshot if source["id"] == input["sourceId"] else source
                    for source in self.stateRef
                ]
            )
            return result

    def current(self) -> list[dict[str, Any]]:
        return self.stateRef

    async def streamChanges(self) -> AsyncIterator[list[dict[str, Any]]]:
        queue: asyncio.Queue[list[dict[str, Any]] | None] = asyncio.Queue()
        self.changes.add(queue)
        previous = self.stateRef
        try:
            yield previous
            while True:
                snapshot = await queue.get()
                if snapshot is None:
                    return
                if snapshot != previous:
                    previous = snapshot
                    yield snapshot
        finally:
            self.changes.discard(queue)

    async def _settingsChanges(self) -> None:
        previous: Any = None
        async for settings in self.settingsService.streamChanges():
            next = settings["usageLimitSources"]
            if next != previous:
                previous = next
                await self.refresh()

    async def _interval(self) -> None:
        while True:
            try:
                try:
                    wait = self.resolveInterval(await self.settingsService.getSettings())
                except Exception:
                    wait = self.defaultInterval
                await asyncio.sleep(60 if wait <= 0 else wait)
                if await self.backgroundPolicy({"type": "provider-status"}):
                    await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                continue

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self._settingsChanges()),
            asyncio.create_task(self._interval()),
            asyncio.create_task(self.refresh()),
        ]

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        for queue in self.changes:
            queue.put_nowait(None)


def make(
    api: CliproxyApi,
    settingsService: SettingsService,
    backgroundPolicy: Callable[[dict[str, str]], Awaitable[bool]],
    resolveInterval: Callable[[dict[str, Any]], float],
    defaultInterval: float,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> UsageLimitSources:
    service = UsageLimitSources(
        api, settingsService, backgroundPolicy, resolveInterval, defaultInterval, now
    )
    service.start()
    return service
