"""Locate the Panels-owned skill packages shipped with planner."""

from __future__ import annotations

from pathlib import Path


def panels_skill_root() -> Path:
    return Path(__file__).resolve().parent / "skills"
