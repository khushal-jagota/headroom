"""Private native homes for synthetic tests, independent of ambient overrides."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path


def validate_destination(root: Path, destination: Path) -> Path:
    """Reject lexical or existing-ancestor escapes from a freshly owned root."""
    canonical_root = root.resolve(strict=True)
    absolute = destination.absolute()
    if not absolute.is_relative_to(root.absolute()):
        raise ValueError(f"synthetic destination escapes owned root: {destination}")
    canonical = absolute.resolve()
    if not canonical.is_relative_to(canonical_root):
        raise ValueError(f"synthetic destination resolves outside owned root: {destination}")
    return absolute


def create_synthetic_homes(storage: Path) -> dict[str, str]:
    """Create, rather than adopt, a private root inside caller-owned test storage."""
    if not storage.is_dir():
        raise ValueError(f"synthetic home storage must already exist: {storage}")
    root = Path(tempfile.mkdtemp(prefix="synthetic-homes-", dir=storage.resolve()))
    home = validate_destination(root, root / "home")
    destinations = {
        "HOME": home,
        "CODEX_HOME": home / ".codex",
        "CLAUDE_CONFIG_DIR": home / ".claude",
        "HERMES_HOME": home / ".hermes",
        "PLAN_HERMES_HOME": home / ".hermes",
    }
    for destination in destinations.values():
        validate_destination(root, destination).mkdir(parents=True, exist_ok=True)
    return {name: str(destination) for name, destination in destinations.items()}


@contextmanager
def synthetic_home_environment(homes: Mapping[str, str]) -> Iterator[None]:
    """Scope parent-process setup without leaking homes into later cases."""
    original = {name: os.environ.get(name) for name in homes}
    os.environ.update(homes)
    try:
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
