"""Hermes-owned employee model discovery and first-session application."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Final

from acp.schema import NewSessionResponse

from planner.tickets.contracts import EmployeeLaunchConfiguration

from .backend_contracts import AcpEmployeeChild, LegacyAcpSessionModelSelection
from .employee_configuration import (
    EmployeeConfigurationCatalog,
    EmployeeConfigurationCatalogOption,
    EmployeeConfigurationError,
)
from .hermes_backend import HERMES_BACKEND_KEY

HERMES_MODEL_CATALOG_TIMEOUT_SECONDS: Final = 30.0


class HermesEmployeeSessionConfigurationAdapter:
    """Use the pinned Hermes installation without importing it into Panels."""

    def __init__(
        self,
        *,
        hermes_python: Path,
        hermes_home: Path,
        hermes_source_root: Path,
        timeout_seconds: float = HERMES_MODEL_CATALOG_TIMEOUT_SECONDS,
        probe_path: Path | None = None,
    ) -> None:
        for field_name, path in (
            ("hermes_python", hermes_python),
            ("hermes_home", hermes_home),
            ("hermes_source_root", hermes_source_root),
        ):
            if not path.is_absolute():
                raise ValueError(f"{field_name} must be absolute")
        if timeout_seconds <= 0:
            raise ValueError("Hermes model catalog timeout must be positive")
        resolved_probe = probe_path or Path(__file__).with_name("hermes_model_catalog_probe.py")
        if not resolved_probe.is_absolute():
            raise ValueError("Hermes model catalog probe path must be absolute")
        self._hermes_python = hermes_python
        self._hermes_home = hermes_home
        self._hermes_source_root = hermes_source_root
        self._timeout_seconds = timeout_seconds
        self._probe_path = resolved_probe

    @property
    def backend_key(self) -> str:
        return HERMES_BACKEND_KEY

    async def discover_catalog(self, candidate_model: str | None) -> EmployeeConfigurationCatalog:
        payload = await self._run_probe()
        try:
            provider, native_model, raw_models = _validate_probe_payload(payload)
            options = _catalog_options(provider, native_model, raw_models)
            if not options:
                raise EmployeeConfigurationError(
                    "Hermes model catalog did not contain any usable models"
                )
            return EmployeeConfigurationCatalog(
                employee_backend=self.backend_key,
                candidate_model=candidate_model,
                native_model=(
                    None if native_model is None else _encode_model(provider, native_model)
                ),
                models=options,
                reasoning_supported=False,
                native_reasoning_effort=None,
                reasoning_efforts=(),
            )
        except EmployeeConfigurationError:
            raise
        except BaseException as error:
            raise EmployeeConfigurationError(
                "Hermes model catalog returned malformed data"
            ) from error

    async def configure_initial_session(
        self,
        child: AcpEmployeeChild,
        response: NewSessionResponse,
        launch_configuration: EmployeeLaunchConfiguration,
    ) -> None:
        if launch_configuration.employee_backend != self.backend_key:
            raise EmployeeConfigurationError(
                "launch configuration does not match the Hermes employee backend"
            )
        if launch_configuration.employee_launch_reasoning_effort is not None:
            raise EmployeeConfigurationError(
                "Hermes does not support employee launch reasoning selection"
            )
        model = launch_configuration.employee_launch_model
        try:
            if model is not None:
                if not isinstance(child, LegacyAcpSessionModelSelection):
                    raise EmployeeConfigurationError(
                        "Hermes ACP child does not support legacy model selection"
                    )
                await child.set_legacy_session_model(response.session_id, model)
            await self.enforce_session_permission_mode(child, response.session_id)
        except asyncio.CancelledError:
            raise
        except EmployeeConfigurationError:
            raise
        except BaseException as error:
            raise EmployeeConfigurationError("Hermes launch model selection failed") from error

    async def enforce_session_permission_mode(
        self, child: AcpEmployeeChild, session_id: str
    ) -> None:
        try:
            await child.set_session_mode(session_id, "dont_ask")
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            raise EmployeeConfigurationError(
                "Hermes session permission configuration failed"
            ) from error

    async def _run_probe(self) -> object:
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._hermes_python),
                str(self._probe_path),
                str(self._hermes_source_root),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={
                    "HERMES_HOME": str(self._hermes_home),
                    "HERMES_PYTHON_SRC_ROOT": str(self._hermes_source_root),
                    "PYTHONIOENCODING": "utf-8",
                },
            )
        except (OSError, ValueError) as error:
            raise EmployeeConfigurationError("Hermes model catalog is unavailable") from error

        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._timeout_seconds
            )
        except asyncio.CancelledError:
            await _retire_probe(process)
            raise
        except TimeoutError as error:
            await _retire_probe(process)
            raise EmployeeConfigurationError("Hermes model catalog is unavailable") from error
        except OSError as error:
            await _retire_probe(process)
            raise EmployeeConfigurationError("Hermes model catalog is unavailable") from error

        if process.returncode != 0:
            raise EmployeeConfigurationError("Hermes model catalog is unavailable")
        try:
            return json.loads(stdout.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise EmployeeConfigurationError(
                "Hermes model catalog returned malformed data"
            ) from error


async def _retire_probe(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        process.kill()
    try:
        await process.communicate()
    except ProcessLookupError:
        return


def _validate_probe_payload(
    payload: object,
) -> tuple[str, str | None, list[object]]:
    if not isinstance(payload, dict) or set(payload) != {
        "provider",
        "nativeModel",
        "models",
    }:
        raise EmployeeConfigurationError("Hermes model catalog returned malformed data")
    provider = payload["provider"]
    native_model = payload["nativeModel"]
    models = payload["models"]
    if (
        not isinstance(provider, str)
        or not provider
        or provider != provider.strip()
        or provider != provider.lower()
        or (native_model is not None and not isinstance(native_model, str))
        or not isinstance(models, list)
    ):
        raise EmployeeConfigurationError("Hermes model catalog returned malformed data")
    if isinstance(native_model, str) and (not native_model or native_model != native_model.strip()):
        raise EmployeeConfigurationError("Hermes model catalog returned malformed data")
    return provider, native_model, models


def _catalog_options(
    provider: str, native_model: str | None, raw_models: list[object]
) -> tuple[EmployeeConfigurationCatalogOption, ...]:
    ordered: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for item in raw_models:
        if not isinstance(item, dict) or set(item) != {"model", "description"}:
            raise EmployeeConfigurationError("Hermes model catalog returned malformed data")
        model = item["model"]
        description = item["description"]
        if (
            not isinstance(model, str)
            or not model
            or model != model.strip()
            or (description is not None and not isinstance(description, str))
            or (isinstance(description, str) and not description.strip())
        ):
            raise EmployeeConfigurationError("Hermes model catalog returned malformed data")
        encoded = _encode_model(provider, model)
        if encoded in seen:
            continue
        seen.add(encoded)
        ordered.append((model, description))

    if native_model is not None:
        encoded_native = _encode_model(provider, native_model)
        if encoded_native not in seen:
            ordered.insert(0, (native_model, None))

    return tuple(
        EmployeeConfigurationCatalogOption(
            value=_encode_model(provider, model),
            label=model,
            description=description,
        )
        for model, description in ordered
    )


def _encode_model(provider: str, model: str) -> str:
    return f"{provider}:{model}"
