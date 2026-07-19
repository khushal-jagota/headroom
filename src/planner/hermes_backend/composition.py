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

from planner.chat import data as chat_data
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core.db import connect
from planner.hermes_backend.employee_child_pool import EmployeeChildPool
from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.relay_tee import RelayTeeObserver
from planner.hermes_backend.transcript_mirror_tee import TranscriptMirrorTee
from planner.minds.gateway import SpawnFn, spawn_popen
from planner.tickets import data as tickets_data


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
    # Persist a fresh stored-session binding after the first create and every rebind, so a
    # restart resumes the LATEST session (not a stale one). Symmetric to the adoption READ
    # below; both use composition's own short-lived connection discipline. S3 §3.2: the
    # callback is a Chief-vs-ticket DISPATCHER — the pool owns EVERY employee's session, and
    # each employee kind persists to its own durable store:
    #   - Chief  → agent_chat_sessions.chat_session_key (record_agent_session_key; no CAS, so
    #     the OLD stored id is ignored).
    #   - ticket → tickets.employee_session_id through the ownership CAS
    #     `bind_pool_employee_session_id`, passing the OLD stored id as `expected` and the new as
    #     `candidate` (Codex Finding 5 — a silent-retain on an expected mismatch raises fail-
    #     closed inside the pool's guarded publication).
    # The callback runs INSIDE the pool's spawn/rebind fail-closed guard, so a persistence
    # failure tears the child down rather than leaving a live-but-unpublished owner (S2B-OWN-001).
    persist_session_binding: Callable[[str, str, str | None], None] | None = None
    if db_path is not None and now is not None:
        persist_db_path = db_path
        persist_now = now

        def persist_session_binding(
            employee_entity_id: str,
            stored_session_id: str,
            old_stored_session_id: str | None = None,
        ) -> None:
            conn = connect(persist_db_path)
            try:
                if employee_entity_id == CHIEF_OF_STAFF_ENTITY_ID:
                    chat_data.record_agent_session_key(
                        conn, employee_entity_id, stored_session_id, persist_now()
                    )
                elif employee_entity_id.startswith("t_"):
                    tickets_data.bind_pool_employee_session_id(
                        conn,
                        employee_entity_id,
                        expected_stored_session_id=old_stored_session_id,
                        candidate_stored_session_id=stored_session_id,
                        now=persist_now(),
                    )
            finally:
                conn.close()

    # On-demand adoption (S3 §3.1): when the pool first spawns an employee with no eagerly-
    # adopted key, it asks this resolver for the employee's persisted durable key so it RESUMEs
    # instead of creating fresh. Composition owns the DB read (the pool stays DB-free): Chief →
    # agent_chat_sessions.chat_session_key; ticket → tickets.employee_session_id. A None/absent
    # return mints a fresh session (a never-run employee), which persistence then binds. Uses
    # composition's own short-lived connection discipline (mirrors _read_chief_session_key).
    stored_session_resolver: Callable[[str], str | None] | None = None
    if db_path is not None:
        resolve_db_path = db_path

        def stored_session_resolver(employee_entity_id: str) -> str | None:
            if employee_entity_id == CHIEF_OF_STAFF_ENTITY_ID:
                return _read_chief_session_key(resolve_db_path)
            if employee_entity_id.startswith("t_"):
                return _read_ticket_employee_session_id(resolve_db_path, employee_entity_id)
            return None

    pool = EmployeeChildPool(
        hermes_python=hermes_python,
        planner_home=planner_home,
        base_env=base_env,
        relay=relay,
        loop=loop,
        spawn=spawn,
        on_stored_session_bound=persist_session_binding,
        stored_session_resolver=stored_session_resolver,
    )
    # Adopt the persisted durable Chief key BEFORE the first spawn so it RESUMES instead
    # of creating fresh (a never-chatted Chief has NULL/absent key -> skipped -> fresh).
    if db_path is not None:
        chief_key = _read_chief_session_key(db_path)
        if chief_key:
            pool.adopt_stored_session(CHIEF_OF_STAFF_ENTITY_ID, chief_key)
    app_state.employee_child_pool = pool
    app_state.employee_child_relay = relay
    app_state.transcript_mirror_tee = mirror
    return pool


def _read_chief_session_key(db_path: str) -> str | None:
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT chat_session_key FROM agent_chat_sessions WHERE id = ?",
            (CHIEF_OF_STAFF_ENTITY_ID,),
        ).fetchone()
        if row is None:
            return None
        key = row["chat_session_key"]
        return key if isinstance(key, str) and key else None
    finally:
        conn.close()


def _read_ticket_employee_session_id(db_path: str, ticket_id: str) -> str | None:
    """The ticket's persisted durable Employee session key (S3 §3.1). None when the ticket is
    absent or has never run (NULL) — so the pool mints a fresh session for it."""
    conn = connect(db_path)
    try:
        row = conn.execute(
            "SELECT employee_session_id FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if row is None:
            return None
        key = row["employee_session_id"]
        return key if isinstance(key, str) and key else None
    finally:
        conn.close()
