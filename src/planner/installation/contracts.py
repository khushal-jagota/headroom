"""Inputs for a new, separate home installation."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class HomeInstall:
    root: Path
    service: str
    port: int
    installer_python: Path
    isolated_providers: bool = False
