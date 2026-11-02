"""Immutable evidence and configuration for the test-only skill guard."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectionInventory:
    roots: tuple[str, ...]
    protected_paths: tuple[str, ...]
    symlink_targets: tuple[tuple[str, str], ...] = ()
    parent_resolutions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    kind: str
    target: str = ""
    sha256: str = ""
    size: int = 0


@dataclass(frozen=True)
class TraversalMetrics:
    seconds: float
    entries: int
    regular_files: int
    hashed_bytes: int


@dataclass(frozen=True)
class Manifest:
    entries: tuple[ManifestEntry, ...]
    metrics: TraversalMetrics


@dataclass(frozen=True)
class ManifestDifference:
    path: str
    change: str


@dataclass(frozen=True)
class RefusalReceipt:
    node: str
    event: str
    path: str
    reason: str


class GuardRefusal(PermissionError):
    """An audited mutation was refused before execution."""
