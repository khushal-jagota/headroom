"""Pure, bounded native build failure values carried in compatible host details."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import cast

from planner.environments.resident.contracts import (
    NativeBuildDiagnostic,
    NativeBuildOperation,
    NativeBuildPhase,
    NativeFailureKind,
    ResidentError,
)

_PREFIX = "native-build-diagnostic:"
_OPERATIONS = {
    "python_probe": {"python_probe"},
    "node_probe": {"node_probe"},
    "python_build": {
        "create_venv", "install_build_tools", "download_source", "build_source_wheel",
        "download_dependencies", "build_application_wheel", "install_wheels",
        "check_dependencies",
    },
    "backend_npm_install": {"npm_ci"},
    "runtime_verification": {
        "import_entrypoint", "installed_files", "check_dependencies", "install_wheels",
    },
}
_KINDS = {"nonzero_exit", "timeout", "process_start", "output_limit", "cleanup_deadline"}


def _validated(value: object) -> NativeBuildDiagnostic:
    if not isinstance(value, dict) or set(value) != {
        "phase", "operation", "failure_kind", "exit_status",
    }:
        raise ResidentError("invalid native build diagnostic")
    phase, operation = value["phase"], value["operation"]
    kind, status = value["failure_kind"], value["exit_status"]
    if (
        not isinstance(phase, str) or phase not in _OPERATIONS
        or not isinstance(operation, str) or operation not in _OPERATIONS[phase]
        or not isinstance(kind, str) or kind not in _KINDS
        or (
            kind == "nonzero_exit"
            and (type(status) is not int or not -255 <= status <= 255 or status == 0)
        )
        or (kind != "nonzero_exit" and status is not None)
    ):
        raise ResidentError("invalid native build diagnostic")
    return NativeBuildDiagnostic(
        cast(NativeBuildPhase, phase), cast(NativeBuildOperation, operation),
        cast(NativeFailureKind, kind), status,
    )


def format_native_diagnostic(diagnostic: NativeBuildDiagnostic) -> str:
    """Serialize only validated fixed process facts; no captured text is admitted."""
    validated = _validated(asdict(diagnostic))
    return _PREFIX + json.dumps(asdict(validated), sort_keys=True, separators=(",", ":"))


def parse_native_diagnostic(detail: str) -> NativeBuildDiagnostic | None:
    """Leave ordinary legacy details intact; refuse malformed reserved diagnostics."""
    if not detail.lstrip().startswith("native-build-diagnostic"):
        return None
    if not detail.startswith(_PREFIX) or len(detail) > 500 or not detail.isprintable():
        raise ResidentError("invalid native build diagnostic")
    try:
        value = json.loads(detail[len(_PREFIX):])
    except (ValueError, RecursionError) as exc:
        raise ResidentError("invalid native build diagnostic") from exc
    diagnostic = _validated(value)
    if detail != format_native_diagnostic(diagnostic):
        raise ResidentError("invalid native build diagnostic")
    return diagnostic
