"""The ordered authority for employee ACP backend registrations."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from planner.conversation.hermes_backend_configuration import (
    hermes_src_root,
    provision_planner_home_skills,
    resolve_hermes_python,
    resolve_planner_home,
)
from planner.core.contracts import ErrorCode, PlannerError

from .backend_contracts import AcpEmployeeChildFactory, AgentBackendDefinition
from .claude_backend import build_claude_employee_backend_registration
from .codex_backend import build_codex_employee_backend_registration
from .employee_configuration import (
    EmployeeSessionConfigurationAdapter,
    NativeEmployeeSessionConfigurationAdapter,
)
from .hermes_backend import HERMES_BACKEND_KEY, build_hermes_acp_backend_definition
from .hermes_employee_configuration import (
    HermesEmployeeSessionConfigurationAdapter,
)
from .hermes_turn_strategy import HermesAcpTurnStrategy
from .sdk_child import SdkAcpEmployeeChildFactory


@dataclass(frozen=True, slots=True)
class EmployeeBackendBuildContext:
    data_directory: Path
    planner_home_default: Path | None = None
    repository_root: Path = field(default_factory=lambda: Path(__file__).resolve().parents[3])

    def __post_init__(self) -> None:
        if not self.repository_root.is_absolute():
            raise ValueError("employee backend repository root must be absolute")


@dataclass(frozen=True, slots=True)
class MaterializedEmployeeBackendRegistration:
    definition: AgentBackendDefinition
    child_factory: AcpEmployeeChildFactory
    is_executable: Callable[[], bool]
    startup_preflight: Callable[[], Awaitable[None]] | None = None
    employee_configuration_adapter: EmployeeSessionConfigurationAdapter | None = None

    def resolved_employee_configuration_adapter(
        self,
    ) -> EmployeeSessionConfigurationAdapter:
        return self.employee_configuration_adapter or NativeEmployeeSessionConfigurationAdapter(
            self.definition.backend_key
        )


EmployeeBackendRuntimeBuilder = Callable[
    [EmployeeBackendBuildContext], MaterializedEmployeeBackendRegistration
]


@dataclass(frozen=True, slots=True)
class EmployeeBackendRegistration:
    backend_key: str
    runtime_builder: EmployeeBackendRuntimeBuilder


class EmployeeBackendCatalog:
    """Immutable ordered registration catalogue shared by domain and runtime."""

    __slots__ = ("_registrations", "_keys")

    def __init__(self, registrations: Iterable[EmployeeBackendRegistration]) -> None:
        ordered = tuple(registrations)
        keys: list[str] = []
        for registration in ordered:
            key = registration.backend_key
            if not isinstance(key, str) or not key or key != key.strip():
                raise ValueError("employee backend key must be non-empty and trimmed")
            if key in keys:
                raise ValueError(f"duplicate employee backend key {key!r}")
            keys.append(key)
        if not ordered:
            raise ValueError("employee backend catalog must not be empty")
        self._registrations = ordered
        self._keys = tuple(keys)

    def registered_backend_keys(self) -> tuple[str, ...]:
        return self._keys

    def is_registered(self, backend_key: str) -> bool:
        return backend_key in self._keys

    def require_registered(self, backend_key: str) -> str:
        if (
            not isinstance(backend_key, str)
            or not backend_key
            or backend_key != backend_key.strip()
        ):
            raise PlannerError(
                ErrorCode.validation,
                "employee backend must be a non-empty registered key",
                {"employee_backend": backend_key},
            )
        if not self.is_registered(backend_key):
            raise PlannerError(
                ErrorCode.validation,
                "unknown employee backend",
                {
                    "employee_backend": backend_key,
                    "employee_backends": list(self._keys),
                },
            )
        return backend_key

    def materialize(
        self, context: EmployeeBackendBuildContext
    ) -> tuple[MaterializedEmployeeBackendRegistration, ...]:
        materialized: list[MaterializedEmployeeBackendRegistration] = []
        for registration in self._registrations:
            built = registration.runtime_builder(context)
            if built.definition.backend_key != registration.backend_key:
                raise ValueError(
                    "materialized employee backend key does not match its registration"
                )
            materialized.append(built)
        return tuple(materialized)


def static_employee_backend_registration(
    definition: AgentBackendDefinition,
    child_factory: AcpEmployeeChildFactory,
    *,
    is_executable: Callable[[], bool] | None = None,
    employee_configuration_adapter: EmployeeSessionConfigurationAdapter | None = None,
) -> EmployeeBackendRegistration:
    built = MaterializedEmployeeBackendRegistration(
        definition=definition,
        child_factory=child_factory,
        is_executable=is_executable or (lambda: True),
        startup_preflight=None,
        employee_configuration_adapter=employee_configuration_adapter,
    )
    return EmployeeBackendRegistration(
        backend_key=definition.backend_key,
        runtime_builder=lambda _context: built,
    )


def _lexical_absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _hermes_executable_for_python(hermes_python: Path) -> Path:
    if not hermes_python.is_absolute():
        raise ValueError("Hermes Python path must be absolute")
    return hermes_python.with_name("hermes")


def _hermes_executable_is_available(executable: Path) -> bool:
    return (
        executable.is_absolute()
        and executable.name == "hermes"
        and executable.is_file()
        and os.access(executable, os.X_OK)
    )


def _materialize_hermes(
    context: EmployeeBackendBuildContext,
) -> MaterializedEmployeeBackendRegistration:
    planner_home = resolve_planner_home(
        default=(
            context.planner_home_default
            if context.planner_home_default is not None
            else context.data_directory / "hermes-home"
        )
    ).resolve(strict=False)
    provision_planner_home_skills(
        planner_home, configured_database_parent=context.data_directory
    )
    hermes_python = _lexical_absolute_path(resolve_hermes_python())
    hermes_executable = _hermes_executable_for_python(hermes_python)
    if not _hermes_executable_is_available(hermes_executable):
        raise FileNotFoundError(
            f"Hermes ACP executable is missing or not executable: {hermes_executable}"
        )
    hermes_source_root = hermes_src_root(hermes_python)
    strategy = HermesAcpTurnStrategy(concurrent_prompt=None, capture_updates=None)
    definition = build_hermes_acp_backend_definition(
        hermes_executable=hermes_executable,
        hermes_home=planner_home,
        hermes_source_root=hermes_source_root,
        turn_strategy=strategy,
    )
    child_factory = SdkAcpEmployeeChildFactory(definition)
    return MaterializedEmployeeBackendRegistration(
        definition=definition,
        child_factory=child_factory,
        is_executable=lambda: _hermes_executable_is_available(hermes_executable),
        startup_preflight=None,
        employee_configuration_adapter=HermesEmployeeSessionConfigurationAdapter(
            hermes_python=hermes_python,
            hermes_home=planner_home,
            hermes_source_root=hermes_source_root,
        ),
    )


PRODUCTION_EMPLOYEE_BACKEND_REGISTRATIONS = (
    EmployeeBackendRegistration(HERMES_BACKEND_KEY, _materialize_hermes),
    build_codex_employee_backend_registration(),
    build_claude_employee_backend_registration(),
)


def build_production_employee_backend_catalog() -> EmployeeBackendCatalog:
    return EmployeeBackendCatalog(PRODUCTION_EMPLOYEE_BACKEND_REGISTRATIONS)
