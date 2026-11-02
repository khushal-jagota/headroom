"""Bounded managed artifact directory pages, independent of Ticket control data."""
from typing import Literal, TypedDict

ArtifactOwnerKind = Literal["ticket", "manager", "goal"]

class ArtifactCatalogEntry(TypedDict):
    key: str
    path: str
    label: str
    kind: str
    href: str | None
    directory: str | None
    child_count: int

class ArtifactDirectoryPage(TypedDict):
    revision: str
    directory: str
    offset: int
    total: int
    entries: list[ArtifactCatalogEntry]
