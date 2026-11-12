"""Durable identity for one local backend child and its optional service unit."""

from dataclasses import dataclass


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


@dataclass(frozen=True, slots=True)
class LegacySourceContext:
    database_path: str
    conversation_id: str
    identity_variables: tuple[tuple[str, str], ...]
    server_url: str | None
