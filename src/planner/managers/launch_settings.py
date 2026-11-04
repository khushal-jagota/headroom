"""Admission for exact Manager launch choices."""

from __future__ import annotations

import sqlite3

from planner.conversation.backend_state import require_enabled_model
from planner.conversation.contracts import ConversationBackendKey
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.managers.contracts import ManagerLaunchConfiguration
from planner.runtime.logic.conversation_start_resolution import (
    ConversationStartOverrides,
    ConversationStartValues,
)
from planner.worker_settings.service import validate_launch_defaults


def launch_configuration_json(configuration: ManagerLaunchConfiguration) -> JsonDict:
    return {
        "employee_backend": configuration.employee_backend.value,
        "employee_launch_model": configuration.employee_launch_model,
        "employee_launch_reasoning_effort": configuration.employee_launch_reasoning_effort,
    }


def parse_launch_configuration(raw: object) -> ManagerLaunchConfiguration:
    defaults = validate_launch_defaults(raw)
    return ManagerLaunchConfiguration(
        ConversationBackendKey(defaults.employee_backend),
        defaults.employee_launch_model,
        defaults.employee_launch_reasoning_effort,
    )


def validate_launch_configuration(
    conn: sqlite3.Connection, configuration: ManagerLaunchConfiguration
) -> None:
    """Reject a known unsupported old default, without an advisory catalog allowlist."""
    if (
        configuration.employee_backend is ConversationBackendKey.codex
        and configuration.employee_launch_model == "sol"
    ):
        raise PlannerError(
            ErrorCode.validation,
            'The legacy Codex model "sol" cannot run. Choose an explicit Codex model '
            "in this Manager's model settings before you retry.",
        )
    require_enabled_model(conn, configuration.employee_backend, configuration.employee_launch_model)


def manager_reuse_overrides(
    conn: sqlite3.Connection,
    manager_id: str,
    values: ConversationStartValues | None,
    overrides: ConversationStartOverrides,
    steer_current_turn: bool,
) -> ConversationStartOverrides:
    """Only explicit settings supersede an existing conversation's current model.

    Historical launch columns do not prove a choice for the next turn. An explicit
    save marks that intent, so an unchanged send cannot restore stale launch metadata.
    """
    from planner.managers.data import read_manager

    item = read_manager(conn, manager_id).item
    if not item.launch_configuration_is_explicit or values is None or steer_current_turn:
        return overrides
    return ConversationStartOverrides(
        backend_key=values.backend_key,
        model=values.model,
        reasoning_effort=values.reasoning_effort,
        reasoning_effort_is_set=True,
    )
