"""Identity of one release installation in an account login session."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MacHomeInstallation:
    uid: int
    root: Path
    label: str
    home_plist: Path
    backup_plist: Path
    installer_python: Path

    def as_dict(self) -> dict[str, object]:
        return {"uid": self.uid, "root": str(self.root), "label": self.label,
                "home_plist": str(self.home_plist), "backup_plist": str(self.backup_plist),
                "installer_python": str(self.installer_python)}
