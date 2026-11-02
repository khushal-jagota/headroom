"""Admission for exact Manager launch choices."""

from __future__ import annotations

import sqlite3

from planner.conversation.backend_state import require_enabled_model
from planner.conversation.contracts import ConversationBackendKey
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.managers.contracts import ManagerLaunchConfiguration
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
