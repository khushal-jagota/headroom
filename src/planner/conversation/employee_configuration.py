"""Backend-neutral discovery and first-session employee configuration."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

from acp.schema import (
    DeniedOutcome,
    NewSessionRequest,
    NewSessionResponse,
    RequestPermissionResponse,
    SessionConfigOptionBoolean,
    SessionConfigOptionSelect,
    SessionConfigSelectGroup,
    SessionConfigSelectOption,
)
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from planner.tickets.contracts import EmployeeLaunchConfiguration

from .backend_contracts import (
    AcpConversationIngress,
    AcpEmployeeChild,
    AcpEmployeeChildFactory,
    AgentBackendDefinition,
)
from .contracts import ConversationEmployee, _require_non_empty_text
from .sdk_child import build_panels_initialize_request

type AcpSessionConfigurationOption = SessionConfigOptionSelect | SessionConfigOptionBoolean


class EmployeeConfigurationError(RuntimeError):
    """A backend could not truthfully discover or apply employee configuration."""


class _EmployeeConfigurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmployeeConfigurationCatalogOption(_EmployeeConfigurationModel):
    value: str
    label: str
    description: str | None = None

    @field_validator("value", "label")
    @classmethod
    def _validate_required_text(cls, value: str, info: object) -> str:
        return _require_non_empty_text(
            value, field_name=str(getattr(info, "field_name", "catalog option"))
        )

    @field_validator("description")
    @classmethod
    def _validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(value, field_name="catalog option description")


class EmployeeConfigurationCatalog(_EmployeeConfigurationModel):
    employee_backend: str
    candidate_model: str | None
    native_model: str | None
    models: tuple[EmployeeConfigurationCatalogOption, ...]
    reasoning_supported: bool
    native_reasoning_effort: str | None
    reasoning_efforts: tuple[EmployeeConfigurationCatalogOption, ...]

    @field_validator("employee_backend")
    @classmethod
    def _validate_backend(cls, value: str) -> str:
        return _require_non_empty_text(value, field_name="employee_backend")

    @field_validator("candidate_model", "native_model", "native_reasoning_effort")
    @classmethod
    def _validate_optional_identifier(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        return _require_non_empty_text(
            value, field_name=str(getattr(info, "field_name", "catalog identifier"))
        )

    @model_validator(mode="after")
    def _validate_reasoning_shape(self) -> EmployeeConfigurationCatalog:
        if self.reasoning_supported:
            if not self.reasoning_efforts:
                raise ValueError("supported reasoning must advertise at least one effort")
        elif self.native_reasoning_effort is not None or self.reasoning_efforts:
            raise ValueError("unsupported reasoning cannot advertise values")
        return self


class EmployeeSessionConfigurationAdapter(Protocol):
    @property
    def backend_key(self) -> str: ...

    async def discover_catalog(
        self, candidate_model: str | None
    ) -> EmployeeConfigurationCatalog: ...

    async def configure_initial_session(
        self,
        child: AcpEmployeeChild,
        response: NewSessionResponse,
        launch_configuration: EmployeeLaunchConfiguration,
    ) -> None: ...

    async def enforce_session_permission_mode(
        self, child: AcpEmployeeChild, session_id: str
    ) -> None: ...


class NativeEmployeeSessionConfigurationAdapter:
    """Keep a backend at its native state until it supplies a real adapter."""

    def __init__(self, backend_key: str) -> None:
        self._backend_key = _require_non_empty_text(
            backend_key, field_name="employee backend"
        )

    @property
    def backend_key(self) -> str:
        return self._backend_key

    async def discover_catalog(
        self, candidate_model: str | None
    ) -> EmployeeConfigurationCatalog:
        if candidate_model is not None:
            raise EmployeeConfigurationError(
                f"employee backend {self.backend_key!r} does not advertise model selection"
            )
        return EmployeeConfigurationCatalog(
            employee_backend=self.backend_key,
            candidate_model=None,
            native_model=None,
            models=(),
            reasoning_supported=False,
            native_reasoning_effort=None,
            reasoning_efforts=(),
        )

    async def configure_initial_session(
        self,
        child: AcpEmployeeChild,
        response: NewSessionResponse,
        launch_configuration: EmployeeLaunchConfiguration,
    ) -> None:
        del child, response
        _require_matching_backend(self.backend_key, launch_configuration)
        if (
            launch_configuration.employee_launch_model is not None
            or launch_configuration.employee_launch_reasoning_effort is not None
        ):
            raise EmployeeConfigurationError(
                f"employee backend {self.backend_key!r} does not accept explicit "
                "launch configuration"
            )

    async def enforce_session_permission_mode(
        self, child: AcpEmployeeChild, session_id: str
    ) -> None:
        del child, session_id


class StableAcpEmployeeSessionConfigurationAdapter:
    """Discover and apply stable ACP select options by semantic category."""

    def __init__(
        self,
        *,
        definition: AgentBackendDefinition,
        child_factory: AcpEmployeeChildFactory,
        workspace_root: Path,
        model_category: str = "model",
        reasoning_category: str = "thought_level",
        full_access_mode: str | None = None,
    ) -> None:
        if not workspace_root.is_absolute():
            raise ValueError("employee configuration workspace root must be absolute")
        self._definition = definition
        self._child_factory = child_factory
        self._workspace_root = workspace_root
        self._model_category = _require_non_empty_text(
            model_category, field_name="model semantic category"
        )
        self._reasoning_category = _require_non_empty_text(
            reasoning_category, field_name="reasoning semantic category"
        )
        self._full_access_mode = full_access_mode

    @property
    def backend_key(self) -> str:
        return self._definition.backend_key

    async def discover_catalog(
        self, candidate_model: str | None
    ) -> EmployeeConfigurationCatalog:
        child: AcpEmployeeChild | None = None
        session_id: str | None = None
        pending_error: BaseException | None = None
        try:
            synthetic_employee_id = f"employee-configuration-{uuid.uuid4().hex}"
            employee = ConversationEmployee(
                employee_id=synthetic_employee_id,
                entity_kind="agent",
                entity_id=synthetic_employee_id,
                workspace_roots=(self._workspace_root,),
                backend_key=self.backend_key,
            )

            async def discard_ingress(_payload: object) -> None:
                return None

            async def deny_permission(_request: object) -> RequestPermissionResponse:
                return RequestPermissionResponse(
                    outcome=DeniedOutcome(outcome="cancelled")
                )

            async def discard_death(_error: BaseException | None) -> None:
                return None

            child = await self._child_factory.create(
                employee,
                1,
                cast(AcpConversationIngress, discard_ingress),
                deny_permission,
                discard_death,
            )
            await child.initialize(build_panels_initialize_request(self._definition))
            response = await child.new_session(self._new_request(employee))
            session_id = response.session_id
            initial_options = tuple(response.config_options or ())
            model_option = _require_semantic_select(
                initial_options, self._model_category
            )
            model_values = _catalog_options(model_option)
            effective_options = initial_options
            if candidate_model is not None:
                _require_advertised_value(model_option, candidate_model)
                updated = await child.set_config_option(
                    session_id, model_option.id, candidate_model
                )
                effective_options = tuple(updated.config_options)
                refreshed_model = _require_semantic_select(
                    effective_options, self._model_category
                )
                if refreshed_model.current_value != candidate_model:
                    raise EmployeeConfigurationError(
                        "ACP model selection did not become the current value"
                    )
            reasoning_option = _optional_semantic_select(
                effective_options, self._reasoning_category
            )
            return EmployeeConfigurationCatalog(
                employee_backend=self.backend_key,
                candidate_model=candidate_model,
                native_model=model_option.current_value,
                models=model_values,
                reasoning_supported=reasoning_option is not None,
                native_reasoning_effort=(
                    None if reasoning_option is None else reasoning_option.current_value
                ),
                reasoning_efforts=(
                    () if reasoning_option is None else _catalog_options(reasoning_option)
                ),
            )
        except asyncio.CancelledError as error:
            pending_error = error
            raise
        except EmployeeConfigurationError as error:
            pending_error = error
            raise
        except BaseException as error:
            pending_error = error
            raise EmployeeConfigurationError(
                f"employee backend {self.backend_key!r} catalog discovery failed"
            ) from error
        finally:
            close_error: BaseException | None = None
            if child is not None and session_id is not None:
                try:
                    await child.close_session(session_id)
                except BaseException as error:
                    close_error = error
            if child is not None:
                try:
                    await child.close()
                except BaseException as error:
                    close_error = close_error or error
            if pending_error is None and close_error is not None:
                raise EmployeeConfigurationError(
                    "temporary ACP configuration discovery session could not be closed"
                ) from close_error

    async def configure_initial_session(
        self,
        child: AcpEmployeeChild,
        response: NewSessionResponse,
        launch_configuration: EmployeeLaunchConfiguration,
    ) -> None:
        try:
            _require_matching_backend(self.backend_key, launch_configuration)
            options = tuple(response.config_options or ())
            explicit_model = launch_configuration.employee_launch_model
            if explicit_model is not None:
                model_option = _require_semantic_select(options, self._model_category)
                _require_advertised_value(model_option, explicit_model)
                updated = await child.set_config_option(
                    response.session_id, model_option.id, explicit_model
                )
                options = tuple(updated.config_options)
                refreshed_model = _require_semantic_select(
                    options, self._model_category
                )
                if refreshed_model.current_value != explicit_model:
                    raise EmployeeConfigurationError(
                        "ACP model selection did not become the current value"
                    )

            explicit_reasoning = (
                launch_configuration.employee_launch_reasoning_effort
            )
            if explicit_reasoning is not None:
                reasoning_option = _require_semantic_select(
                    options, self._reasoning_category
                )
                _require_advertised_value(reasoning_option, explicit_reasoning)
                updated = await child.set_config_option(
                    response.session_id, reasoning_option.id, explicit_reasoning
                )
                refreshed_reasoning = _require_semantic_select(
                    tuple(updated.config_options), self._reasoning_category
                )
                if refreshed_reasoning.current_value != explicit_reasoning:
                    raise EmployeeConfigurationError(
                        "ACP reasoning selection did not become the current value"
                    )
            await self.enforce_session_permission_mode(child, response.session_id)
        except asyncio.CancelledError:
            raise
        except EmployeeConfigurationError:
            raise
        except BaseException as error:
            raise EmployeeConfigurationError(
                f"employee backend {self.backend_key!r} launch configuration failed"
            ) from error

    async def enforce_session_permission_mode(
        self, child: AcpEmployeeChild, session_id: str
    ) -> None:
        if self._full_access_mode is None:
            return
        try:
            await child.set_session_mode(session_id, self._full_access_mode)
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            raise EmployeeConfigurationError(
                f"employee backend {self.backend_key!r} session permission "
                "configuration failed"
            ) from error

    def _new_request(self, employee: ConversationEmployee) -> NewSessionRequest:
        cwd = self._definition.working_directory_for(employee)
        additional = [
            str(root) for root in employee.workspace_roots if root != cwd
        ]
        return NewSessionRequest(
            cwd=str(cwd), additional_directories=additional, mcp_servers=[]
        )


class EmployeeConfigurationCatalogService:
    """Cache truthful backend catalogs for one server process."""

    def __init__(
        self, adapters: Mapping[str, EmployeeSessionConfigurationAdapter]
    ) -> None:
        self._adapters = dict(adapters)
        if not self._adapters:
            raise ValueError("employee configuration adapters must not be empty")
        for backend_key, adapter in self._adapters.items():
            if backend_key != adapter.backend_key:
                raise ValueError(
                    "employee configuration adapter key does not match its backend"
                )
        self._cache: dict[tuple[str, str | None], EmployeeConfigurationCatalog] = {}
        self._lock = asyncio.Lock()

    async def catalog(
        self, employee_backend: str, candidate_model: str | None
    ) -> EmployeeConfigurationCatalog:
        key = (employee_backend, candidate_model)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            adapter = self._adapters[employee_backend]
        except KeyError:
            raise EmployeeConfigurationError(
                f"unknown employee backend {employee_backend!r}"
            ) from None
        async with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            discovered = await adapter.discover_catalog(candidate_model)
            if (
                discovered.employee_backend != employee_backend
                or discovered.candidate_model != candidate_model
            ):
                raise EmployeeConfigurationError(
                    "employee configuration adapter returned a mismatched catalog"
                )
            self._cache[key] = discovered
            return discovered


def _require_matching_backend(
    backend_key: str, launch_configuration: EmployeeLaunchConfiguration
) -> None:
    if launch_configuration.employee_backend != backend_key:
        raise EmployeeConfigurationError(
            "launch configuration does not match the selected employee backend"
        )


def _require_semantic_select(
    options: Sequence[AcpSessionConfigurationOption], category: str
) -> SessionConfigOptionSelect:
    matches = [option for option in options if option.category == category]
    if len(matches) != 1 or not isinstance(matches[0], SessionConfigOptionSelect):
        raise EmployeeConfigurationError(
            f"ACP session must advertise exactly one select option in category {category!r}"
        )
    option = matches[0]
    _catalog_options(option)
    return option


def _optional_semantic_select(
    options: Sequence[AcpSessionConfigurationOption], category: str
) -> SessionConfigOptionSelect | None:
    matches = [option for option in options if option.category == category]
    if not matches:
        return None
    return _require_semantic_select(options, category)


def _catalog_options(
    option: SessionConfigOptionSelect,
) -> tuple[EmployeeConfigurationCatalogOption, ...]:
    flattened: list[SessionConfigSelectOption] = []
    for item in option.options:
        if isinstance(item, SessionConfigSelectGroup):
            flattened.extend(item.options)
        elif isinstance(item, SessionConfigSelectOption):
            flattened.append(item)
        else:  # pragma: no cover - ACP's typed union currently makes this unreachable.
            raise EmployeeConfigurationError("ACP select option has an invalid value")
    values = [item.value for item in flattened]
    if not flattened or len(values) != len(set(values)):
        raise EmployeeConfigurationError(
            "ACP select option values must be non-empty and unique"
        )
    if option.current_value not in set(values):
        raise EmployeeConfigurationError(
            "ACP select option current value is not advertised"
        )
    try:
        return tuple(
            EmployeeConfigurationCatalogOption(
                value=item.value,
                label=item.name,
                description=item.description or None,
            )
            for item in flattened
        )
    except ValueError as error:
        raise EmployeeConfigurationError("ACP select option is malformed") from error


def _require_advertised_value(
    option: SessionConfigOptionSelect, value: str
) -> None:
    advertised = {item.value for item in _catalog_options(option)}
    if value not in advertised:
        raise EmployeeConfigurationError(
            f"ACP session does not advertise selected value {value!r}"
        )
