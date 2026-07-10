"""Standalone resolution of the Hermes interpreter, source root, and planner
home, plus a boot smoke-check. Deliberately independent of planner.core.config
(this wave is additive; wiring into the core Config is a later wave).
Nothing calls boot_smoke_check this wave; it is unit-tested via the fake
spawn hook only — never with a real child inside pytest."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from planner.minds.gateway import READY_TIMEOUT_DEFAULT, GatewayChild, SpawnFn, spawn_popen

DEFAULT_HERMES_PYTHON: Final = "~/.hermes/hermes-agent/venv/bin/python"
DEFAULT_PLANNER_HOME: Final = "data/hermes-home"
PLANNER_SKILL_NAMES: Final = ("panels", "panels-worker", "panels-chief-of-staff")

ENV_HERMES_PYTHON: Final = "PLAN_HERMES_PYTHON"
ENV_PLANNER_HOME: Final = "PLAN_HERMES_HOME"


def resolve_hermes_python(
    value: str | None = None, env: Mapping[str, str] | None = None
) -> Path:
    """Resolve the Hermes interpreter: explicit value → env → default; then expanduser."""
    source = env if env is not None else os.environ
    chosen = value or source.get(ENV_HERMES_PYTHON) or DEFAULT_HERMES_PYTHON
    return Path(chosen).expanduser()


def hermes_src_root(hermes_python: Path | str) -> Path:
    """~/.hermes/hermes-agent/venv/bin/python → ~/.hermes/hermes-agent.

    Assumes the standard venv layout (parents[2]); a path too shallow for that
    degrades to .parent. The result only feeds the HERMES_PYTHON_SRC_ROOT
    sys.path hint (entry.py:4-12), so a weird path degrades gracefully.
    """
    path = Path(hermes_python).expanduser()
    try:
        return path.parents[2]
    except IndexError:
        return path.parent


def resolve_planner_home(
    value: str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    default: Path | str = DEFAULT_PLANNER_HOME,
) -> Path:
    """Resolve the planner home: explicit value → env → default; then expanduser."""
    source = env if env is not None else os.environ
    chosen = value or source.get(ENV_PLANNER_HOME) or default
    return Path(chosen).expanduser()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def provision_planner_home_skills(
    home: Path | str,
    skill_names: tuple[str, ...] = PLANNER_SKILL_NAMES,
) -> None:
    """Expose this repo's role skills inside the configured Hermes home."""
    source_root = repo_root() / "skills"
    target_root = Path(home).expanduser() / "skills"
    target_root.mkdir(parents=True, exist_ok=True)
    for skill_name in skill_names:
        source = source_root / skill_name
        if not source.is_dir():
            raise FileNotFoundError(f"planner skill not found: {source}")
        target = target_root / skill_name
        if target.is_symlink():
            if target.resolve() == source.resolve():
                continue
            target.unlink()
        if target.exists():
            continue
        target.symlink_to(source, target_is_directory=True)


def boot_smoke_check(
    hermes_python: Path | None = None,
    *,
    spawn: SpawnFn = spawn_popen,
    ready_timeout: float = READY_TIMEOUT_DEFAULT,
    env: Mapping[str, str] | None = None,
) -> None:
    """Spawn → gateway.ready → clean exit. Raises GatewayError on any failure."""
    python = hermes_python if hermes_python is not None else resolve_hermes_python()
    base = dict(env if env is not None else os.environ)
    base["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(python))
    child = GatewayChild(str(python), base, spawn=spawn)
    try:
        child.wait_ready(ready_timeout)
    finally:
        child.shutdown()
