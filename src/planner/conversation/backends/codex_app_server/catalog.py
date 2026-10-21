"""Child-scoped, independent catalogue sources and bounded recovery."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.catalog_config import (
    APP_HINT_COOLDOWN_SECONDS,
    APP_METADATA_BATCH_SIZE,
    APP_METADATA_TTL_SECONDS,
    CATALOG_DIAGNOSTIC_MAXIMUM_CHARACTERS,
    CATALOG_REFRESH_COALESCE_SECONDS,
    CATALOG_RETRY_INITIAL_SECONDS,
    CATALOG_RETRY_MAXIMUM_SECONDS,
)
from planner.conversation.backends.codex_app_server.client import (
    CodexRequestRejected,
    CodexWireFailed,
)

LOGGER = logging.getLogger("planner.conversation.backends.codex_app_server")


@dataclass
class _Lane:
    pending: bool = False
    failures: int = 0
    deadline: float = 0.0
    event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class CatalogCoordinator:
    """Source failures never block prompt delivery or another source's recovery."""

    def __init__(
        self,
        *,
        read_skills: Callable[[], Awaitable[bindings.SkillsListResponse]],
        read_plugins: Callable[[], Awaitable[bindings.PluginInstalledResponse]],
        read_installed: Callable[[], Awaitable[bindings.AppsInstalledResponse]],
        read_metadata: Callable[[list[str]], Awaitable[bindings.AppsReadResponse]],
        publish: Callable[[], Awaitable[None]],
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock or time.monotonic
        self._reads: dict[str, Callable[[], Awaitable[Any]]] = {
            "skills": read_skills,
            "plugins": read_plugins,
            "installed": read_installed,
            "metadata": self._refresh_metadata,
        }
        self._read_metadata = read_metadata
        self._publish = publish
        self._lanes = {name: _Lane() for name in self._reads}
        self.skills: bindings.SkillsListResponse | None = None
        self.plugins: bindings.PluginInstalledResponse | None = None
        self.installed: bindings.AppsInstalledResponse | None = None
        self.metadata: dict[str, tuple[float, bindings.ConnectorMetadata | None]] = {}
        self._identities: frozenset[str] = frozenset()
        self._generation = 0
        self.closed = False

    def start(self) -> None:
        if self.closed:
            return
        for name, lane in self._lanes.items():
            if lane.task is None:
                lane.task = asyncio.create_task(self._run(name), name=f"codex.catalog.{name}")
        for name in ("skills", "plugins", "installed"):
            self._request(name, coalesce=False)

    def _request(self, name: str, *, coalesce: bool = True) -> None:
        if self.closed:
            return
        lane = self._lanes[name]
        if not lane.pending and coalesce:
            lane.deadline = max(lane.deadline, self._clock() + CATALOG_REFRESH_COALESCE_SECONDS)
        lane.pending = True
        lane.event.set()

    def hint_apps(self) -> None:
        self._request("installed")

    def hint_skills(self) -> None:
        self._request("skills")
        self._request("plugins")

    def wake(self) -> None:
        """Re-evaluate monotonic deadlines (also permits deterministic clock tests)."""
        for lane in self._lanes.values():
            lane.event.set()

    async def close(self) -> None:
        self.closed = True
        tasks = [lane.task for lane in self._lanes.values() if lane.task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, name: str) -> None:
        lane = self._lanes[name]
        while not self.closed:
            lane.event.clear()
            delay = lane.deadline - self._clock()
            if not lane.pending or delay > 0:
                try:
                    if lane.pending:
                        await asyncio.wait_for(lane.event.wait(), timeout=delay)
                    else:
                        await lane.event.wait()
                except TimeoutError:
                    pass
                continue
            lane.pending = False
            read_succeeded = False
            try:
                result = await self._reads[name]()
                if self.closed:
                    return
                if name == "skills":
                    self.skills = result
                elif name == "plugins":
                    self.plugins = result
                elif name == "installed":
                    self._set_installed(result)
                read_succeeded = True
                lane.deadline = (
                    self._clock() + APP_HINT_COOLDOWN_SECONDS if name == "installed" else 0.0
                )
            except asyncio.CancelledError:
                raise
            except Exception as failure:
                # A failed runtime source immediately revokes its entries. Metadata is
                # display-only, so its failure keeps the latest usable cached labels.
                if name == "skills":
                    self.skills = None
                elif name == "plugins":
                    self.plugins = None
                elif name == "installed":
                    self._set_installed(None)
                lane.failures += 1
                delay = min(
                    CATALOG_RETRY_MAXIMUM_SECONDS,
                    CATALOG_RETRY_INITIAL_SECONDS * 2 ** min(lane.failures - 1, 16),
                )
                lane.deadline = self._clock() + delay
                lane.pending = True
                _log_failure(name, failure, delay)
            try:
                await self._publish()
                if read_succeeded:
                    lane.failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as failure:
                # A sink failure must not silently retire a source lane. A source
                # failure already scheduled and logged this attempt's bounded retry.
                if not read_succeeded:
                    continue
                # Retain valid source data while retrying a failed publication.
                lane.failures += 1
                delay = min(
                    CATALOG_RETRY_MAXIMUM_SECONDS,
                    CATALOG_RETRY_INITIAL_SECONDS * 2 ** min(lane.failures - 1, 16),
                )
                lane.deadline = self._clock() + delay
                lane.pending = True
                _log_failure(name, failure, delay)

    def _set_installed(self, installed: bindings.AppsInstalledResponse | None) -> None:
        self.installed = installed
        identities = frozenset(
            app.id
            for app in (() if installed is None else installed.apps)
            if app.enabled and app.callable
        )
        if identities != self._identities:
            self._generation += 1
            self._identities = identities
        self.metadata = {
            key: value
            for key, value in self.metadata.items()
            if key in identities and self._clock() - value[0] < APP_METADATA_TTL_SECONDS
        }
        if identities:
            self._request("metadata", coalesce=False)

    async def _refresh_metadata(self) -> None:
        generation = self._generation
        now = self._clock()
        missing = sorted(
            key
            for key in self._identities
            if key not in self.metadata or now - self.metadata[key][0] >= APP_METADATA_TTL_SECONDS
        )
        for offset in range(0, len(missing), APP_METADATA_BATCH_SIZE):
            ids = missing[offset : offset + APP_METADATA_BATCH_SIZE]
            response = await self._read_metadata(ids)
            if self.closed or generation != self._generation:
                return
            returned = {app.id: app for app in response.apps}
            for app_id in ids:
                self.metadata[app_id] = (self._clock(), returned.get(app_id))


def _log_failure(source: str, failure: Exception, delay: float) -> None:
    # Only fixed categories and a numeric RPC code leave this boundary. Exception text
    # may contain response bodies, HTML, credentials, or Pydantic validation inputs.
    category = "request"
    code = ""
    if isinstance(failure, CodexRequestRejected):
        category = "rpc"
        if type(failure.code) is int:
            code = f" code={failure.code}"
    elif isinstance(failure, ValidationError):
        category = "validation"
    elif isinstance(failure, CodexWireFailed):
        category = "wire"
    elif isinstance(failure, TimeoutError):
        category = "timeout"
    line = f"Codex catalogue {source} failed kind={category}{code} retry={delay:g}s"
    LOGGER.warning("%s", line[:CATALOG_DIAGNOSTIC_MAXIMUM_CHARACTERS])
