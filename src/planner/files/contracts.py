"""Contracts for managed ticket files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TicketFile:
    ticket_id: str
    relative_path: str
    absolute_path: Path

