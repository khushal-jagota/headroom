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
) -> EmployeeLaunchConfiguration:
    """What a Ticket is created running on, from its Worker type and what its creator named.

    A creator that names nothing gets the Worker type's launch defaults whole. One that
    names a model gets that model, on whichever backend it named. What it cannot do is
    name a backend other than the Worker type's own and leave the model to the defaults:
    a model id belongs to the backend that gave it, so there would be nothing left for the
    Ticket to run on, and that is refused rather than written half-answered.

    The reasoning effort only comes along when the backend and model are the Worker type's
    own, because it was chosen for that model and says nothing about any other.

    Raises ``PlannerError`` when a different backend is named with no model for it.
    """
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
        employee_launch_reasoning_effort=default_reasoning_effort if the_types_own else None,
    )


def normalize_employee_launch_configuration(
    current: EmployeeLaunchConfiguration,
    candidate: EmployeeLaunchConfiguration,
    *,
    advertised_models: frozenset[str] | None,
    reasoning_supported: bool | None,
    advertised_reasoning_efforts: frozenset[str] | None,
) -> EmployeeLaunchConfiguration:
    """Validate a same-backend snapshot, or take a backend change whole.

    Every candidate names a model, whichever it is. A backend change is taken as it
    stands: there is no catalog to check it against, because the one this Ticket was on
    is the wrong backend's and the candidate is the caller's own naming of the new one.
    A same-backend change is checked against that backend's catalog, and a model change
    carries an old reasoning selection harmlessly: if the new model does not advertise it,
    the value normalizes to the new model's own (null).

    Catalog inputs may be absent only for a no-op or a backend change.
    """

    if candidate == current:
        return current
    model = candidate.employee_launch_model
    if model is None or not model.strip():
        raise PlannerError(
            ErrorCode.validation,
            "a Ticket's launch configuration has to name a model",
            {"employee_backend": candidate.employee_backend},
        )
    if candidate.employee_backend != current.employee_backend:
        return candidate
    if advertised_models is None or reasoning_supported is None:
        raise RuntimeError("same-backend Employee configuration change requires a catalog")

    if model not in advertised_models:
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
