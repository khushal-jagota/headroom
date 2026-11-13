"""A model choice is refused when it is switched off or its machine does not offer it."""

from sqlite3 import Connection

import pytest

from planner.conversation.backend_state import (
    disabled_models,
    save_machine_model_list,
    write_model_enablement,
)
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.errors import PlannerError
from planner.execution_identity import TicketLocale
from planner.tickets import data as tickets


def test_configuration_commit_rechecks_disables_without_catalog_evidence(
    tmp_db: Connection,
) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Configuration race",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
    )
    expected = tickets.employee_launch_configuration(ticket)
    write_model_enablement(tmp_db, Backend.codex, "gpt-6-sol", False)

    def commit_configuration() -> None:
        tickets.write_employee_configuration(
            tmp_db,
            ticket.id,
            employee_backend="codex",
            employee_launch_model="gpt-6-sol",
            employee_launch_reasoning_effort="high",
            now=2,
        )

    with pytest.raises(PlannerError, match="disabled"):
        commit_configuration()
    assert tickets.employee_launch_configuration(tickets.read_ticket(tmp_db, ticket.id)) == expected
    write_model_enablement(tmp_db, Backend.codex, "gpt-6-sol", True)
    commit_configuration()
    current = tickets.read_ticket(tmp_db, ticket.id)
    assert current.employee_launch_model == "gpt-6-sol"
    assert current.employee_launch_reasoning_effort == "high"


def _model_list(backend: Backend, *model_ids: str, complete: bool = True) -> BackendSnapshot:
    return BackendSnapshot(
        backend_key=backend,
        installed=True,
        executable_path=f"/usr/bin/{backend.value}",
        version="1.0.0",
        identity=None,
        available_models=tuple(BackendModel(model_id) for model_id in model_ids),
        reasoning_effort_options=(),
        default_model_id=model_ids[0] if model_ids else None,
        default_reasoning_effort=None,
        update_advisory=None,
        diagnoses=(),
        models_complete=complete,
    )


def _create(conn: Connection, backend: str, model: str, locale: str = "vps") -> str:
    return (
        tickets.create_ticket(
            conn,
            title="Model choice",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            worker_type="coding",
            locale=locale,
            employee_backend=backend,
            employee_launch_model=model,
        ).employee_launch_model
        or ""
    )


def test_a_model_the_machine_does_not_list_is_refused_with_its_choices(
    tmp_db: Connection,
) -> None:
    # Risk: a made-up model name is saved and the Worker's first turn fails on the
    # backend with a misleading error. Nothing compared a name with the backend's list.
    save_machine_model_list(
        tmp_db,
        TicketLocale.macos,
        _model_list(Backend.codex, "gpt-6.1-sol", "gpt-6-luna", "gpt-6-sol"),
    )
    write_model_enablement(tmp_db, Backend.codex, "gpt-6-sol", False)

    with pytest.raises(PlannerError) as refused:
        _create(tmp_db, "codex", "gpt-6.1-luna", locale="macos")
    assert refused.value.message == (
        'Model "gpt-6.1-luna" is not offered by codex on the Mac. '
        "Choose one of: gpt-6.1-sol, gpt-6-luna."
    )
    assert _create(tmp_db, "codex", "gpt-6-luna", locale="macos") == "gpt-6-luna"
    # The VPS has no list of its own yet, so the backend stays the judge there.
    assert _create(tmp_db, "codex", "gpt-6.1-luna") == "gpt-6.1-luna"
    # An incomplete list is no evidence that a model is gone.
    save_machine_model_list(
        tmp_db, TicketLocale.vps, _model_list(Backend.codex, "x", complete=False)
    )
    assert _create(tmp_db, "codex", "gpt-6.1-luna") == "gpt-6.1-luna"


def test_claude_short_names_and_long_context_forms_count_as_offered(tmp_db: Connection) -> None:
    # Risk: live Tickets saved on `opus`, `fable[1m]` and `claude-fable-5[1m]` are refused,
    # because the Claude CLI accepts these names but never lists them.
    save_machine_model_list(
        tmp_db,
        TicketLocale.vps,
        _model_list(Backend.claude, "claude-opus-5-5", "claude-fable-5", "claude-sonnet-5-5[1m]"),
    )
    for accepted in (
        "claude-opus-5-5",
        "opus",
        "opus[1m]",
        "fable[1m]",
        "claude-fable-5[1m]",
        "sonnet",
        "claude-sonnet-5-5",
    ):
        assert _create(tmp_db, "claude", accepted) == accepted
    for refused in ("claude-opus-5-6", "haiku", "claude-opus-5-5[2m]"):
        with pytest.raises(PlannerError, match="not offered by claude on the VPS"):
            _create(tmp_db, "claude", refused)


def test_a_complete_list_retires_switch_offs_no_machine_still_offers(tmp_db: Connection) -> None:
    # Risk: switch-off rows for retired models pile up in the catalog, or a row is
    # deleted while a machine without a list would still run that model.
    for model in ("gpt-5.5", "gpt-5.6-sol", "gpt-6-sol"):
        write_model_enablement(tmp_db, Backend.codex, model, False)
    save_machine_model_list(tmp_db, TicketLocale.vps, _model_list(Backend.codex, "gpt-6-sol"))
    assert disabled_models(tmp_db, Backend.codex) == {"gpt-5.5", "gpt-5.6-sol", "gpt-6-sol"}

    save_machine_model_list(tmp_db, TicketLocale.macos, _model_list(Backend.codex, "gpt-5.6-sol"))

    assert disabled_models(tmp_db, Backend.codex) == {"gpt-5.6-sol", "gpt-6-sol"}
