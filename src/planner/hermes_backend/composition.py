"""The config-gated composition helper. Extracts the exact composition `_lifespan`
performs so the gate test drives the SAME code path at `test_mode=False`, varying only
the flag (plan §1, R-13). This is the only import site for EmployeeChildPool/
EmployeeChildRelay; server.py does NOT import them directly (R-12).

S2a additive wiring: when a db_path + now clock are supplied (the real lifespan does),
construct the TranscriptMirrorTee and register it as the relay's tee observer — the first
product consumer of S1's seam. This is additive; it does not change the relay's public
behavior. The gate tests that omit db_path/now compose exactly as S1 did (no tee).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.relay_tee import RelayTeeObserver
from planner.hermes_backend.transcript_mirror_tee import TranscriptMirrorTee
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
    db_path: str | None = None,
    now: Callable[[], int] | None = None,
) -> EmployeeChildPool | None:
    if not config.relay_backend_enabled:
        return None
    tee_observers: tuple[RelayTeeObserver, ...] = ()
    mirror: TranscriptMirrorTee | None = None
    if db_path is not None and now is not None:
        mirror = TranscriptMirrorTee(db_path=db_path, now=now)
        mirror.start()
        tee_observers = (mirror,)
    relay = EmployeeChildRelay(
        pool_provider=lambda: app_state.employee_child_pool,
        loop=loop,
        tee_observers=tee_observers,
    )
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
    app_state.transcript_mirror_tee = mirror
    return pool
