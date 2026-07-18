"""The config-gated composition helper. Extracts the exact composition `_lifespan`
performs so the gate test drives the SAME code path at `test_mode=False`, varying only
the flag (plan §1, R-13). This is the only import site for EmployeeChildPool/
EmployeeChildRelay; server.py does NOT import them directly (R-12)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.minds.gateway import SpawnFn, spawn_popen


def compose_relay_backend_if_enabled(
    *,
    config: Any,
    app_state: Any,
    loop: asyncio.AbstractEventLoop,
    hermes_python: Path,
    planner_home: Path,
    base_env: Mapping[str, str],
    spawn: SpawnFn = spawn_popen,
) -> EmployeeChildPool | None:
    if not config.relay_backend_enabled:
        return None
    relay = EmployeeChildRelay(pool_provider=lambda: app_state.employee_child_pool, loop=loop)
    pool = EmployeeChildPool(
        hermes_python=hermes_python,
        planner_home=planner_home,
        base_env=base_env,
        relay=relay,
        loop=loop,
        spawn=spawn,
    )
    app_state.employee_child_pool = pool
    app_state.employee_child_relay = relay
    return pool
