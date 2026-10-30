"""Pure normalization rules for one Ticket's first-session launch request.

A backend and a model only mean anything together: a model id belongs to the backend
that named it, and a backend with no model runs on whatever it picked for itself, which
is a value nobody chose and nobody here can see. So every rule in this file moves the
two as a pair — a Ticket that changes backend names the new backend's model in the same
breath, and one that cannot name a model is refused rather than written half-answered.
"""

from __future__ import annotations

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import EmployeeLaunchConfiguration


def launch_configuration_for_a_new_ticket(
    *,
    default_backend: str,
    default_model: str,
    default_reasoning_effort: str | None,
    employee_backend: str,
    employee_launch_model: str | None,
    employee_launch_reasoning_effort: str | None = None,
    reasoning_effort_is_set: bool = False,
) -> EmployeeLaunchConfiguration:
    """What a Ticket is created running on, from its Worker type and what its creator named.

    A creator that names nothing gets the Worker type's launch defaults whole. One that
    names a model gets that model, on whichever backend it named. What it cannot do is
    name a backend other than the Worker type's own and leave the model to the defaults:
    a model id belongs to the backend that gave it, so there would be nothing left for the
    Ticket to run on, and that is refused rather than written half-answered.

    Inherited reasoning effort only comes along when the backend and model are the
    Worker type's own. Explicit null requests the provider default. An explicit
    effort remains the exact provider request.

    Raises ``PlannerError`` when a different backend is named with no model for it.
    """
    if employee_launch_reasoning_effort is not None and (
        not isinstance(employee_launch_reasoning_effort, str)
        or not employee_launch_reasoning_effort.strip()
    ):
        raise PlannerError(
            ErrorCode.validation, "employee_launch_reasoning_effort must be a nonblank string"
        )
    if employee_launch_model is None or not employee_launch_model.strip():
        if employee_backend != default_backend:
            raise PlannerError(
                ErrorCode.validation,
                "a Ticket created on a different backend has to name that backend's model",
                {"employee_backend": employee_backend},
            )
        model = default_model
    else:
        model = employee_launch_model
    the_types_own = employee_backend == default_backend and model == default_model
    return EmployeeLaunchConfiguration(
        employee_backend=employee_backend,
        employee_launch_model=model,
        employee_launch_reasoning_effort=(
            employee_launch_reasoning_effort
            if employee_launch_reasoning_effort is not None or reasoning_effort_is_set
            else default_reasoning_effort
            if the_types_own
            else None
        ),
    )


def normalize_employee_launch_configuration(
    current: EmployeeLaunchConfiguration,
    candidate: EmployeeLaunchConfiguration,
    *,
    advertised_models: frozenset[str] | None,
    reasoning_supported: bool | None,
    advertised_reasoning_efforts: frozenset[str] | None,
) -> EmployeeLaunchConfiguration:
    """Preserve authored intent independently of advisory model suggestions."""

    if candidate == current:
        return current
    model = candidate.employee_launch_model
    if model is None or not model.strip():
        raise PlannerError(
            ErrorCode.validation,
            "a Ticket's launch configuration has to name a model",
            {"employee_backend": candidate.employee_backend},
        )
    reasoning_effort = candidate.employee_launch_reasoning_effort
    if reasoning_effort is not None and not reasoning_effort.strip():
        raise PlannerError(
            ErrorCode.validation, "employee_launch_reasoning_effort must be a nonblank string"
        )

    return EmployeeLaunchConfiguration(
        employee_backend=candidate.employee_backend,
        employee_launch_model=model,
        employee_launch_reasoning_effort=reasoning_effort,
    )
