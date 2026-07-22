from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
from acp.schema import NewSessionResponse
from tests.support.acp_in_memory_binding_repository import (
    InMemoryAcpBindingRepository,
)
from tests.unit.test_acp_employee_registry import (
    _definition as _registry_definition,
)
from tests.unit.test_acp_employee_registry import (
    _employee as _registry_employee,
)
from tests.unit.test_acp_employee_registry import (
    _FakeChild as _RegistryFakeChild,
)
from tests.unit.test_acp_employee_registry import (
    _FakeFactory as _RegistryFakeFactory,
)
from tests.unit.test_acp_employee_registry import _registry as _build_registry

from planner.conversation.contracts import ConversationEmployee, ConversationSessionBinding
from planner.conversation.employee_configuration import (
    EmployeeConfigurationCatalog,
    EmployeeConfigurationCatalogOption,
    EmployeeConfigurationCatalogService,
    EmployeeConfigurationError,
)
from planner.conversation.hermes_employee_configuration import (
    HermesEmployeeSessionConfigurationAdapter,
)
from planner.tickets.contracts import EmployeeLaunchConfiguration


def _adapter(
    tmp_path: Path, *, timeout_seconds: float = 1
) -> HermesEmployeeSessionConfigurationAdapter:
    return HermesEmployeeSessionConfigurationAdapter(
        hermes_python=Path(sys.executable).resolve(),
        hermes_home=(tmp_path / "hermes-home").resolve(),
        hermes_source_root=(tmp_path / "hermes-source").resolve(),
        timeout_seconds=timeout_seconds,
    )


def _install_fake_hermes_source(
    tmp_path: Path,
    *,
    provider: str | None = "Provider Alias",
    native_model: str | None = "active-model",
) -> Path:
    source_root = (tmp_path / "hermes-source").resolve()
    package = source_root / "hermes_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    model_fields = []
    if provider is not None:
        model_fields.append(f"'provider': {provider!r}")
    if native_model is not None:
        model_fields.append(f"'default': {native_model!r}")
    expected_home = str((tmp_path / "hermes-home").resolve())
    (package / "config.py").write_text(
        "import os\n"
        "def load_config_readonly():\n"
        f"    assert os.environ['HERMES_HOME'] == {expected_home!r}\n"
        f"    return {{'model': {{{', '.join(model_fields)}}}}}\n",
        encoding="utf-8",
    )
    (package / "models.py").write_text(
        "def normalize_provider(provider):\n"
        "    return {'Provider Alias': 'provider'}.get(provider, provider.lower())\n"
        "def curated_models_for_provider(provider):\n"
        "    assert provider == 'provider'\n"
        "    return [\n"
        "        ('catalog-first', 'First description'),\n"
        "        ('catalog-second', ''),\n"
        "        ('catalog-first', 'Duplicate ignored'),\n"
        "    ]\n",
        encoding="utf-8",
    )
    return source_root


def test_hermes_catalog_uses_exact_read_only_installation_and_server_cache(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        source_root = _install_fake_hermes_source(tmp_path)
        adapter = _adapter(tmp_path)
        service = EmployeeConfigurationCatalogService({"hermes": adapter})
        repository = InMemoryAcpBindingRepository()

        first = await service.catalog("hermes", "provider:catalog-second")
        # Removing the probe's source after the first result proves the second
        # call is the MR-01 server-lifetime cache, not another subprocess.
        (source_root / "hermes_cli" / "models.py").unlink()
        second = await service.catalog("hermes", "provider:catalog-second")

        assert first is second
        assert first == EmployeeConfigurationCatalog(
            employee_backend="hermes",
            candidate_model="provider:catalog-second",
            native_model="provider:active-model",
            models=(
                EmployeeConfigurationCatalogOption(
                    value="provider:active-model",
                    label="active-model",
                    description=None,
                ),
                EmployeeConfigurationCatalogOption(
                    value="provider:catalog-first",
                    label="catalog-first",
                    description="First description",
                ),
                EmployeeConfigurationCatalogOption(
                    value="provider:catalog-second",
                    label="catalog-second",
                    description=None,
                ),
            ),
            reasoning_supported=False,
            native_reasoning_effort=None,
            reasoning_efforts=(),
        )
        assert await repository.resolve("employee-never-created") is None

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("script", "timeout_seconds", "error_match"),
    [
        (
            "import sys\nprint('credential=must-not-leak', file=sys.stderr)\nsys.exit(3)\n",
            1,
            "catalog is unavailable",
        ),
        ("print('not-json')\n", 1, "malformed data"),
        ("import time\ntime.sleep(30)\n", 0.01, "catalog is unavailable"),
    ],
)
def test_hermes_catalog_process_failures_are_calm_and_uncached(
    tmp_path: Path,
    script: str,
    timeout_seconds: float,
    error_match: str,
) -> None:
    async def exercise() -> None:
        probe = (tmp_path / "probe.py").resolve()
        probe.write_text(script, encoding="utf-8")
        adapter = HermesEmployeeSessionConfigurationAdapter(
            hermes_python=Path(sys.executable).resolve(),
            hermes_home=(tmp_path / "hermes-home").resolve(),
            hermes_source_root=(tmp_path / "hermes-source").resolve(),
            timeout_seconds=timeout_seconds,
            probe_path=probe,
        )
        service = EmployeeConfigurationCatalogService({"hermes": adapter})

        for _attempt in range(2):
            with pytest.raises(EmployeeConfigurationError, match=error_match) as raised:
                await service.catalog("hermes", None)
            assert "credential" not in str(raised.value)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "payload",
    [
        {"provider": "provider", "nativeModel": None, "models": []},
        {"provider": "provider", "nativeModel": None, "models": "bad"},
        {
            "provider": "provider",
            "nativeModel": None,
            "models": [{"model": "", "description": None}],
        },
    ],
)
def test_hermes_catalog_rejects_empty_or_malformed_probe_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    async def exercise() -> None:
        adapter = _adapter(tmp_path)
        calls = 0

        async def return_payload() -> object:
            nonlocal calls
            calls += 1
            return payload

        monkeypatch.setattr(adapter, "_run_probe", return_payload)
        service = EmployeeConfigurationCatalogService({"hermes": adapter})
        for _attempt in range(2):
            with pytest.raises(EmployeeConfigurationError):
                await service.catalog("hermes", None)
        assert calls == 2

    asyncio.run(exercise())


def test_hermes_probe_rejects_missing_configured_provider(tmp_path: Path) -> None:
    async def exercise() -> None:
        _install_fake_hermes_source(tmp_path, provider=None)
        with pytest.raises(EmployeeConfigurationError, match="catalog is unavailable"):
            await _adapter(tmp_path).discover_catalog(None)

    asyncio.run(exercise())


class _LegacyModelChild:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.requests: list[tuple[str, str]] = []
        self.mode_requests: list[tuple[str, str]] = []
        self.failure = failure

    async def set_legacy_session_model(self, session_id: str, model_id: str) -> None:
        self.requests.append((session_id, model_id))
        if self.failure is not None:
            raise self.failure

    async def set_session_mode(self, session_id: str, mode_id: str) -> None:
        self.mode_requests.append((session_id, mode_id))


def test_hermes_launch_applies_only_explicit_model_and_rejects_reasoning_first(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        adapter = _adapter(tmp_path)
        child = _LegacyModelChild()
        response = NewSessionResponse(session_id="hermes-first")

        await adapter.configure_initial_session(
            child,  # type: ignore[arg-type]
            response,
            EmployeeLaunchConfiguration(
                employee_backend="hermes",
                employee_launch_model=None,
                employee_launch_reasoning_effort=None,
            ),
        )
        assert child.requests == []
        assert child.mode_requests == [("hermes-first", "dont_ask")]

        await adapter.configure_initial_session(
            child,  # type: ignore[arg-type]
            response,
            EmployeeLaunchConfiguration(
                employee_backend="hermes",
                employee_launch_model="provider:selected",
                employee_launch_reasoning_effort=None,
            ),
        )
        assert child.requests == [("hermes-first", "provider:selected")]
        assert child.mode_requests == [
            ("hermes-first", "dont_ask"),
            ("hermes-first", "dont_ask"),
        ]

        with pytest.raises(EmployeeConfigurationError, match="does not support"):
            await adapter.configure_initial_session(
                child,  # type: ignore[arg-type]
                response,
                EmployeeLaunchConfiguration(
                    employee_backend="hermes",
                    employee_launch_model="provider:never-sent",
                    employee_launch_reasoning_effort="high",
                ),
            )
        assert child.requests == [("hermes-first", "provider:selected")]

    asyncio.run(exercise())


def test_hermes_launch_wraps_legacy_request_failure(tmp_path: Path) -> None:
    async def exercise() -> None:
        adapter = _adapter(tmp_path)
        child = _LegacyModelChild(failure=RuntimeError("wire failed"))
        with pytest.raises(EmployeeConfigurationError, match="selection failed"):
            await adapter.configure_initial_session(
                child,  # type: ignore[arg-type]
                NewSessionResponse(session_id="hermes-first"),
                EmployeeLaunchConfiguration(
                    employee_backend="hermes",
                    employee_launch_model="provider:selected",
                    employee_launch_reasoning_effort=None,
                ),
            )
        assert child.requests == [("hermes-first", "provider:selected")]

    asyncio.run(exercise())


def test_hermes_launch_requires_the_separate_legacy_model_capability(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        with pytest.raises(EmployeeConfigurationError, match="does not support"):
            await _adapter(tmp_path).configure_initial_session(
                object(),  # type: ignore[arg-type]
                NewSessionResponse(session_id="hermes-first"),
                EmployeeLaunchConfiguration(
                    employee_backend="hermes",
                    employee_launch_model="provider:selected",
                    employee_launch_reasoning_effort=None,
                ),
            )

    asyncio.run(exercise())


class _LegacyRegistryChild(_RegistryFakeChild):
    async def set_legacy_session_model(self, session_id: str, model_id: str) -> None:
        if self.operation_observer is not None:
            self.operation_observer(f"set_legacy_session_model:{session_id}:{model_id}")
        if model_id == "provider:fail":
            raise RuntimeError("scripted legacy request failure")


class _LegacyRegistryFactory(_RegistryFakeFactory):
    async def create(
        self,
        employee: ConversationEmployee,
        generation: int,
        update_ingress: Any,
        permission_callback: Any,
        death_callback: Any,
    ) -> _LegacyRegistryChild:
        del employee, permission_callback
        self.creation_started.set()
        if self.creation_gate is not None:
            await self.creation_gate.wait()
        child = _LegacyRegistryChild(
            generation=generation,
            definition=self.definition,
            update_ingress=update_ingress,
            death_callback=death_callback,
            initialize_gate=self.initialize_gate,
            load_gate=self.load_gate,
            new_session_gate=self.new_session_gate,
            fail_load=self.fail_load,
            close_error=self.close_error,
            operation_observer=self.operation_observer,
        )
        child.private_replay_by_session.update(self.private_replay_by_session)
        child.fail_private_load_session_ids.update(self.fail_private_load_session_ids)
        child.private_load_errors_by_session.update(self.private_load_errors_by_session)
        child.private_load_gate = self.private_load_gate
        self.children.append(child)
        return child


def test_hermes_model_is_applied_once_before_first_binding_and_never_reapplied(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        definition = _registry_definition("hermes")
        operations: list[str] = []
        factory = _LegacyRegistryFactory(definition)
        factory.operation_observer = operations.append
        repository = InMemoryAcpBindingRepository()
        employee = _registry_employee(backend_key="hermes").model_copy(
            update={
                "employee_launch_model": "provider:selected",
                "employee_launch_reasoning_effort": None,
            }
        )

        async def compare_initial(
            candidate: ConversationSessionBinding,
            configuration: EmployeeLaunchConfiguration,
        ) -> ConversationSessionBinding:
            assert configuration.employee_launch_model == "provider:selected"
            operations.append("publish_first_binding")
            return await repository.compare_and_swap_initial(candidate, configuration)

        registry = _build_registry(
            {"hermes": definition},
            {"hermes": factory},
            repository,
            configuration_adapters={"hermes": _adapter(tmp_path)},
            compare_and_swap_initial=compare_initial,
        )

        first = await registry.get_or_spawn(employee)
        assert operations[:5] == [
            "initialize",
            "new_session",
            "set_legacy_session_model:hermes-1-1:provider:selected",
            "set_session_mode:hermes-1-1:dont_ask",
            "publish_first_binding",
        ]
        assert first.employee.employee_launch_model is None

        operations.append("before_requested_cancel_replacement")
        handle = await registry.resolve_runtime_handle("employee-a", 1)
        deadline = asyncio.get_running_loop().time() + 2
        requested_cancel = await registry.replace_runtime_after_requested_cancel(
            await registry.acquire_runtime_lease(handle), deadline
        )

        operations.append("before_compaction")
        prepared = await registry.prepare_compaction_capture(
            await registry.acquire_runtime_lease(requested_cancel.replacement_handle),
            "hermes-no-reapply",
            deadline,
        )
        compacted = await registry.commit_compaction_capture(prepared, deadline)

        operations.append("before_new_conversation")
        replacement = await registry.new_conversation(employee)
        assert compacted.replacement_handle.binding.binding_generation == 2
        assert replacement.binding.binding_generation == 3
        assert sum(item.startswith("set_legacy_session_model:") for item in operations) == 1

        operations.append("before_bound_reload")
        await registry.fail_runtime_handle(replacement)
        await asyncio.sleep(0)
        loaded = await registry.get_or_spawn(employee)
        assert loaded.binding == replacement.binding
        assert sum(item.startswith("set_legacy_session_model:") for item in operations) == 1
        assert [child.mode_requests for child in factory.children] == [
            [("hermes-1-1", "dont_ask")],
            [("hermes-1-1", "dont_ask")],
            [
                ("hermes-1-1-fork-1", "dont_ask"),
                ("hermes-3-1", "dont_ask"),
            ],
            [("hermes-3-1", "dont_ask")],
        ]
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())


def test_hermes_model_request_failure_creates_no_binding_or_prompt(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        definition = _registry_definition("hermes")
        factory = _LegacyRegistryFactory(definition)
        repository = InMemoryAcpBindingRepository()
        registry = _build_registry(
            {"hermes": definition},
            {"hermes": factory},
            repository,
            configuration_adapters={"hermes": _adapter(tmp_path)},
        )
        employee = _registry_employee(backend_key="hermes").model_copy(
            update={"employee_launch_model": "provider:fail"}
        )

        with pytest.raises(EmployeeConfigurationError, match="selection failed"):
            await registry.get_or_spawn(employee)

        assert await repository.resolve(employee.employee_id) is None
        assert len(factory.children) == 1
        assert factory.children[0].alive is False
        await registry.shutdown(asyncio.get_running_loop().time() + 1)

    asyncio.run(exercise())
