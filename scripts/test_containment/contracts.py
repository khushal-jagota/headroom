"""Test boundary transport. A contract describes evidence, never grants admission."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CONTRACT_PATH = Path("/run/panels-test-containment/contract.json")


class ContainmentRefusal(RuntimeError):
    """Kernel evidence cannot establish the required test boundary."""


@dataclass(frozen=True)
class BackingMount:
    major_minor: str
    root: str


@dataclass(frozen=True)
class ContainmentContract:
    scratch_root: Path
    scratch_mount: BackingMount
    protected_paths: tuple[Path, ...]
    namespace_ids: tuple[tuple[str, str], ...]
    provider_profile_paths: tuple[Path, ...] = ()
    provider_profile_mounts: tuple[tuple[Path, BackingMount], ...] = ()
