"""Monotonic logical input deadlines, independent of physical I/O settlement."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

from planner.executor.configuration import (
    INPUT_FINALIZATION_TIMEOUT_SECONDS,
    INPUT_PROGRESS_TIMEOUT_SECONDS,
)
from planner.executor.contracts import (
    ExecutorIdentity,
    FrameWriter,
    InputTransferProgress,
    InputTransferRequest,
    WireMessage,
)


@dataclass
class InputTransferDeadline:
    request: InputTransferRequest
    phase: Literal["download", "finalization"] = "download"
    byte_count: int = 0
    deadline: float = field(
        default_factory=lambda: asyncio.get_running_loop().time() + INPUT_PROGRESS_TIMEOUT_SECONDS
    )
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    revoked: bool = False

    def advance(self, progress: InputTransferProgress) -> None:
        if (
            progress.command_id != self.request.command_id
            or progress.transfer_id != self.request.slot.transfer_id
            or progress.byte_count < self.byte_count
            or progress.byte_count > self.request.slot.manifest.byte_count
        ):
            raise ValueError("invalid input progress identity or byte count")
        if progress.phase not in ("download", "finalization"):
            raise ValueError("invalid input phase")
        if not self.active():
            return
        if progress.phase == "download":
            if self.phase != "download":
                raise ValueError("download progress after finalization")
            if progress.byte_count > self.byte_count:
                self.byte_count = progress.byte_count
                self.deadline = asyncio.get_running_loop().time() + INPUT_PROGRESS_TIMEOUT_SECONDS
                self.changed.set()
        else:
            if progress.byte_count != self.request.slot.manifest.byte_count:
                raise ValueError("finalization before complete bytes")
            if self.phase == "download":
                self.phase = "finalization"
                self.byte_count = progress.byte_count
                self.deadline = (
                    asyncio.get_running_loop().time() + INPUT_FINALIZATION_TIMEOUT_SECONDS
                )
                self.changed.set()

    def active(self) -> bool:
        if asyncio.get_running_loop().time() >= self.deadline:
            self.revoked = True
        return not self.revoked

    async def expired(self) -> None:
        while True:
            self.changed.clear()
            remaining = self.deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self.changed.wait(), remaining)
            except TimeoutError:
                return


@dataclass
class InputTransferExchange:
    deadline: InputTransferDeadline
    identity: ExecutorIdentity
    writer: FrameWriter
    response: asyncio.Future[WireMessage]
    cancelled: asyncio.Event = field(default_factory=asyncio.Event)
