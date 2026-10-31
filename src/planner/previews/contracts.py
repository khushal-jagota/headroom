"""Shared preview registry and transport contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class PreviewError(Exception):
    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    boot_id: str
    pid: int
    start_ticks: int
    listener_inode: int
    port: int
    worktree: str


@dataclass(frozen=True, slots=True)
class PreviewRecord:
    ticket_id: str
    generation: str
    worktree: str
    public_origin: str
    server_epoch: str
    created_at: int
    state: Literal["prepared", "active", "ended"]
    ended_reason: str | None = None
    process: ProcessIdentity | None = None

    @property
    def base_path(self) -> str:
        return f"/dev/tickets/{self.ticket_id}/{self.generation}/"

    @property
    def url(self) -> str:
        return self.public_origin + self.base_path

    @property
    def port(self) -> int:
        if self.process is None:
            raise PreviewError("Preview application is not registered.", 410)
        return self.process.port


@dataclass(frozen=True, slots=True)
class TicketEligibility:
    ticket_id: str
    project_folder: Path | None
    terminal: bool
