"""Durable identity for one local backend child and its optional service unit."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class LocalChildIdentity:
    pid: int
    birth: str
    boot: str
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class LocalHostBootIdentity:
    host: str
    boot: str


type RecordedSourceSettlement = Literal["retired", "undelivered"]
