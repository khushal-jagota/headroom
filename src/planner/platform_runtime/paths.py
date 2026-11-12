"""Installation paths shared by existing home and executor configuration."""
from pathlib import Path


def installation_data_directory(root: Path) -> Path:
    return root / "data"
