"""Pure normalization rules for one Ticket's first-session launch request."""

from __future__ import annotations

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import EmployeeLaunchConfiguration


def normalize_employee_launch_configuration(
    current: EmployeeLaunchConfiguration,
    candidate: EmployeeLaunchConfiguration,
    *,
    advertised_models: frozenset[str] | None,
    reasoning_supported: bool | None,
    advertised_reasoning_efforts: frozenset[str] | None,
) -> EmployeeLaunchConfiguration:
    """Validate a same-backend snapshot or clear dependencies on backend/model change.

    Catalog inputs may be absent only for a no-op or a backend change. A model change
    carries an old reasoning selection harmlessly: if the new model does not advertise
    it, the value normalizes to the new model's native default (null).
    """

    if candidate == current:
        return current
    if candidate.employee_backend != current.employee_backend:
        return EmployeeLaunchConfiguration(candidate.employee_backend, None, None)
    if advertised_models is None or reasoning_supported is None:
        raise RuntimeError("same-backend Employee configuration change requires a catalog")

    model = candidate.employee_launch_model
    if model is not None and model not in advertised_models:
        raise PlannerError(
            ErrorCode.validation,
            "Employee model is not available",
            {
                "employee_backend": candidate.employee_backend,
                "employee_launch_model": model,
            },
        )

    reasoning_effort = candidate.employee_launch_reasoning_effort
    advertised_reasoning = advertised_reasoning_efforts or frozenset()
    if reasoning_effort is not None and (
        not reasoning_supported or reasoning_effort not in advertised_reasoning
    ):
        if model != current.employee_launch_model:
            reasoning_effort = None
        else:
            raise PlannerError(
                ErrorCode.validation,
                "Employee reasoning effort is not available",
                {
                    "employee_backend": candidate.employee_backend,
                    "employee_launch_model": model,
                    "employee_launch_reasoning_effort": reasoning_effort,
                },
            )

    return EmployeeLaunchConfiguration(
        employee_backend=candidate.employee_backend,
        employee_launch_model=model,
        employee_launch_reasoning_effort=reasoning_effort,
    )

