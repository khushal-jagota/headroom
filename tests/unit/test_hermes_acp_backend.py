from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from acp.transports import default_environment

from planner.conversation import (
    HERMES_ACP_AGENT_VERSION,
    HERMES_BACKEND_KEY,
    HERMES_INHERITED_ENVIRONMENT_NAMES,
    ConversationEmployee,
    EmployeeBackendBuildContext,
    HermesEmployeeSessionConfigurationAdapter,
    backend_catalog,
    build_confined_child_environment,
    build_hermes_acp_backend_definition,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class _HermesStrategy:
    async def steer(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def observe_compaction(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def capture_compaction(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


def _employee(entity_kind: str = "ticket") -> ConversationEmployee:
    return ConversationEmployee(
        employee_id="employee-hermes",
        entity_kind=entity_kind,  # type: ignore[arg-type]
        entity_id="ticket-hermes" if entity_kind == "ticket" else "chief",
        workspace_roots=(Path("/workspace/first"), Path("/workspace/second")),
        backend_key="hermes",
    )


def _definition(strategy: _HermesStrategy | None = None) -> Any:
    return build_hermes_acp_backend_definition(
        hermes_executable=Path("/opt/hermes/bin/hermes"),
        hermes_home=Path("/srv/panels/hermes-home"),
        hermes_source_root=Path("/opt/hermes/source"),
        turn_strategy=strategy or _HermesStrategy(),
    )


def test_hermes_definition_is_exact_and_strategy_is_injected() -> None:
    strategy = _HermesStrategy()
    definition = _definition(strategy)
    assert definition.backend_key == HERMES_BACKEND_KEY == "hermes"
    assert definition.argv == ("/opt/hermes/bin/hermes", "acp")
    assert definition.expected_agent_name == "hermes-agent"
    assert definition.expected_agent_version == HERMES_ACP_AGENT_VERSION == "0.18.2"
    assert definition.turn_capabilities.supports_steer is True
    assert definition.turn_capabilities.observes_compaction is True
    assert definition.turn_capabilities.requires_fresh_child_after_requested_cancel is False
    assert definition.reverse_service_capabilities.filesystem is False
    assert definition.reverse_service_capabilities.terminal is False
    assert definition.reverse_service_capabilities.permission is True
    assert definition.inherited_environment_names == HERMES_INHERITED_ENVIRONMENT_NAMES
    assert definition.environment_overrides == (
        ("HERMES_HOME", "/srv/panels/hermes-home"),
        ("HERMES_PYTHON_SRC_ROOT", "/opt/hermes/source"),
    )
    assert definition.working_directory_for(_employee()) == Path("/workspace/first")
    assert definition.turn_strategy is strategy


def test_hermes_environment_has_explicit_overrides_and_exact_panels_identity() -> None:
    ambient = {
        **default_environment(),
        "OPENAI_API_KEY": "no",
        "ANTHROPIC_API_KEY": "no",
        "PLAN_TICKET_ID": "stale",
        "PLAN_ACTOR": "stale",
        "HERMES_HOME": "/ambient",
        "HERMES_TUI_SKILLS": "on",
    }
    ticket_environment = build_confined_child_environment(
        _definition(), _employee(), ambient_environment=ambient
    )
    assert ticket_environment["HERMES_HOME"] == "/srv/panels/hermes-home"
    assert ticket_environment["HERMES_PYTHON_SRC_ROOT"] == "/opt/hermes/source"
    assert ticket_environment["PLAN_TICKET_ID"] == "ticket-hermes"
    assert ticket_environment["PLAN_ACTOR"] == "worker"
    assert ticket_environment["HERMES_YOLO_MODE"] == "1"
    assert "OPENAI_API_KEY" not in ticket_environment
    assert "ANTHROPIC_API_KEY" not in ticket_environment
    assert "HERMES_TUI_SKILLS" not in ticket_environment

    chief_environment = build_confined_child_environment(
        _definition(), _employee("agent"), ambient_environment=ambient
    )
    assert chief_environment["PLAN_ACTOR"] == "chief"
    assert chief_environment["HERMES_YOLO_MODE"] == "1"
    assert "PLAN_TICKET_ID" not in chief_environment


@pytest.mark.parametrize(
    ("keyword", "path"),
    [
        ("executable", Path("relative/hermes")),
        ("home", Path("relative/home")),
        ("source", Path("relative/source")),
    ],
)
def test_hermes_definition_rejects_relative_paths(keyword: str, path: Path) -> None:
    values = {
        "hermes_executable": Path("/opt/hermes/bin/hermes"),
        "hermes_home": Path("/srv/hermes"),
        "hermes_source_root": Path("/opt/hermes/source"),
    }
    values[
        {
            "executable": "hermes_executable",
            "home": "hermes_home",
            "source": "hermes_source_root",
        }[keyword]
    ] = path
    with pytest.raises(ValueError, match="absolute"):
        build_hermes_acp_backend_definition(
            **values,  # type: ignore[arg-type]
            turn_strategy=_HermesStrategy(),
        )


def test_production_hermes_registration_reuses_exact_resolved_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source_root = (tmp_path / "hermes-source").resolve()
    hermes_python = source_root / "venv" / "bin" / "python"
    hermes_executable = hermes_python.with_name("hermes")
    hermes_executable.parent.mkdir(parents=True)
    hermes_python.write_text("", encoding="utf-8")
    hermes_executable.write_text("", encoding="utf-8")
    hermes_executable.chmod(0o755)
    planner_home = (tmp_path / "planner-hermes-home").resolve()
    provisioned: list[tuple[Path, Path | None]] = []
    monkeypatch.setattr(backend_catalog, "resolve_hermes_python", lambda: hermes_python)
    monkeypatch.setattr(
        backend_catalog,
        "provision_planner_home_skills",
        lambda home, *, configured_database_parent=None: provisioned.append(
            (
                Path(home),
                (
                    None
                    if configured_database_parent is None
                    else Path(configured_database_parent)
                ),
            )
        ),
    )

    registration = next(
        item
        for item in backend_catalog.PRODUCTION_EMPLOYEE_BACKEND_REGISTRATIONS
        if item.backend_key == HERMES_BACKEND_KEY
    )
    materialized = registration.runtime_builder(
        EmployeeBackendBuildContext(
            data_directory=tmp_path,
            planner_home_default=planner_home,
            repository_root=REPOSITORY_ROOT,
        )
    )
    adapter = materialized.employee_configuration_adapter
    assert isinstance(adapter, HermesEmployeeSessionConfigurationAdapter)
    assert materialized.definition.argv == (str(hermes_executable), "acp")
    assert materialized.definition.environment_overrides == (
        ("HERMES_HOME", str(planner_home)),
        ("HERMES_PYTHON_SRC_ROOT", str(source_root)),
    )
    assert adapter._hermes_python == hermes_python  # noqa: SLF001
    assert adapter._hermes_home == planner_home  # noqa: SLF001
    assert adapter._hermes_source_root == source_root  # noqa: SLF001
    assert provisioned == [(planner_home, tmp_path)]


def test_generic_runtime_has_no_backend_name_conditional() -> None:
    for filename in ("sdk_child.py", "ordered_ingress.py", "employee_registry.py"):
        source = (REPOSITORY_ROOT / "src" / "planner" / "conversation" / filename).read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp, ast.Match)):
                continue
            constants = {
                value.value.lower()
                for value in ast.walk(node)
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            }
            assert "hermes" not in constants, f"backend conditional leaked into {filename}"
