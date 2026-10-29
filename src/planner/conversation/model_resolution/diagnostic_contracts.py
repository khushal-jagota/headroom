"""Finite owner-only diagnostic receipt; no private capture data belongs here."""
from typing import Literal

from planner.executor.contracts import JsonObject

DiagnosticStatus = Literal[
    "compared", "capture_missing", "limit_documents", "limit_candidates", "limit_nodes",
    "limit_depth", "limit_string", "limit_integer", "limit_scalars", "limit_sequence",
    "limit_work", "limit_time", "cancelled", "invalidated", "arm_expired", "internal_error",
]
_STATUSES = (
    "compared", "capture_missing", "limit_documents", "limit_candidates", "limit_nodes",
    "limit_depth", "limit_string", "limit_integer", "limit_scalars", "limit_sequence",
    "limit_work", "limit_time", "cancelled", "invalidated", "arm_expired", "internal_error",
)

DiagnosticState = Literal["equal", "changed", "unavailable"]
_ROLES = ("selected_global_state", "settings", "managed_settings", "remote_policy_cache")
_ROLE_NAMES = ("global_state", "settings", "managed_settings", "remote_policy_cache")
_FACTS = ("executable_identity", "executable_version", "launch_environment", "workspace",
          "executor_identity")
_OTHER = ("source_selection", "inactive_global_candidate", "candidate_directory",
          "credential_metadata", "provider_source_snapshot", "ordinary_file_metadata",
          "raw_byte_equality")
_STATES = ("equal", "changed", "unavailable")


def unavailable_record(status: DiagnosticStatus = "internal_error") -> JsonObject:
    return {
        "diagnostic_status": status,
        **dict.fromkeys((*_FACTS, *_OTHER), "unavailable"),
        "roles": {role: {"source_validity": "unavailable", "semantic_content": "unavailable"}
                  for role in _ROLES},
    }


def validate_record(value: object) -> JsonObject:
    """Reject nonfinite or extended evidence before any output serialization."""
    template = unavailable_record()
    if type(value) is not dict or len(value) != len(template):
        raise ValueError("Invalid diagnostic record")
    if any(type(key) is not str or len(key) > 32 for key in value):
        raise ValueError("Invalid diagnostic record")
    if value.keys() != template.keys():
        raise ValueError("Invalid diagnostic record")
    status = value["diagnostic_status"]
    if type(status) is not str or len(status) > 16 or status not in _STATUSES:
        raise ValueError("Invalid diagnostic record")
    template["diagnostic_status"] = status
    for key in (*_FACTS, *_OTHER):
        state = value[key]
        if type(state) is not str or len(state) > 11 or state not in _STATES:
            raise ValueError("Invalid diagnostic record")
        template[key] = state
    roles = value["roles"]
    if type(roles) is not dict or len(roles) != len(_ROLES):
        raise ValueError("Invalid diagnostic record")
    if any(type(key) is not str or len(key) > 32 for key in roles) or roles.keys() != set(_ROLES):
        raise ValueError("Invalid diagnostic record")
    clean_roles: JsonObject = {}
    for role in _ROLES:
        states = roles[role]
        if type(states) is not dict or len(states) != 2:
            raise ValueError("Invalid diagnostic record")
        if (any(type(key) is not str or len(key) > 32 for key in states)
                or states.keys() != {"source_validity", "semantic_content"}):
            raise ValueError("Invalid diagnostic record")
        clean: JsonObject = {}
        for key in ("source_validity", "semantic_content"):
            state = states[key]
            if type(state) is not str or len(state) > 11 or state not in _STATES:
                raise ValueError("Invalid diagnostic record")
            clean[key] = state
        clean_roles[role] = clean
    template["roles"] = clean_roles
    return template
