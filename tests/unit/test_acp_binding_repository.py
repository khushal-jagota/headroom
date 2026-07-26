from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from tests.support.acp_in_memory_binding_repository import (
    InMemoryAcpBindingRepository,
)

from planner.conversation import sqlite_binding_repository as binding_repository_module
from planner.conversation.backend_catalog import (
    EmployeeBackendCatalog,
    EmployeeBackendRegistration,
    build_production_employee_backend_catalog,
)
from planner.conversation.contracts import (
    CHIEF_OF_STAFF_ENTITY_ID,
    ContextCompactionTrigger,
    ConversationCompactionBoundaryProvenance,
    ConversationSessionBinding,
)
from planner.conversation.sqlite_binding_repository import (
    ConversationBindingError,
)
from planner.conversation.sqlite_binding_repository import (
    SqliteConversationBindingRepository as _SqliteConversationBindingRepository,
)
from planner.core.db import connect, create_schema
from planner.tickets.contracts import EmployeeLaunchConfiguration
from planner.worker_settings import service as worker_settings_service
from planner.worker_types.configuration import (
    configured_worker_type_registry,
)


class _TestSqliteConversationBindingRepository(_SqliteConversationBindingRepository):
    async def compare_and_swap(
        self,
        expected: ConversationSessionBinding | None,
        candidate: ConversationSessionBinding,
    ) -> ConversationSessionBinding:
        if expected is not None:
            return await super().compare_and_swap(expected, candidate)
        employee = await self.resolve_employee(candidate.employee_id)
        if employee.entity_kind == "agent":
            return await super().compare_and_swap(expected, candidate)
        return await self.compare_and_swap_initial(
            candidate,
            EmployeeLaunchConfiguration(
                employee_backend=employee.backend_key,
                employee_launch_model=employee.employee_launch_model,
                employee_launch_reasoning_effort=(employee.employee_launch_reasoning_effort),
            ),
        )


def SqliteConversationBindingRepository(
    db_path: str,
    *,
    workspace_root: Path,
    integer_now: Callable[[], int],
    employee_backend_catalog: EmployeeBackendCatalog | None = None,
    chief_backend_key: str = "hermes",
) -> _SqliteConversationBindingRepository:
    return _TestSqliteConversationBindingRepository(
        db_path,
        workspace_root=workspace_root,
        integer_now=integer_now,
        employee_backend_catalog=(
            employee_backend_catalog or build_production_employee_backend_catalog()
        ),
        chief_backend_key=chief_backend_key,
    )


def _catalog(*backend_keys: str) -> EmployeeBackendCatalog:
    def unmaterialized(_context):
        raise AssertionError("binding tests must not materialize runtimes")

    return EmployeeBackendCatalog(
        EmployeeBackendRegistration(backend_key, unmaterialized) for backend_key in backend_keys
    )


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    employee_session_id: str | None = None,
    employee_backend: str = "hermes",
    employee_launch_model: str | None = None,
    employee_launch_reasoning_effort: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, employee_launch_model, "
        "employee_launch_reasoning_effort, stage, ceiling, fields, employee_session_id, "
        "created_at, updated_at) VALUES (?, ?, 'coding', ?, ?, ?, "
        "'needs_kickoff', 'needs_kickoff', ?, ?, 10, 20)",
        (
            ticket_id,
            ticket_id,
            employee_backend,
            employee_launch_model,
            employee_launch_reasoning_effort,
            json.dumps(
                {
                    field: {"value": None, "proposal": None, "user_note": None}
                    for field in (
                        "kickoff",
                        "success",
                        "approach",
                        "plan",
                        "implementation",
                        "closeout",
                    )
                },
                separators=(",", ":"),
            ),
            employee_session_id,
        ),
    )


def test_initial_binding_rejects_session_prepared_from_stale_ticket_configuration(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stale-employee-configuration.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(
        conn,
        "t_alpha",
        employee_launch_model="model-new",
        employee_launch_reasoning_effort="high",
    )
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    candidate = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-stale",
        backend_key="hermes",
        binding_generation=1,
    )

    with pytest.raises(ConversationBindingError, match="changed before first binding"):
        asyncio.run(
            repository.compare_and_swap_initial(
                candidate,
                EmployeeLaunchConfiguration(
                    employee_backend="hermes",
                    employee_launch_model="model-old",
                    employee_launch_reasoning_effort="low",
                ),
            )
        )

    conn = connect(str(db_path))
    assert (
        conn.execute("SELECT employee_session_id FROM tickets WHERE id = 't_alpha'").fetchone()[
            "employee_session_id"
        ]
        is None
    )
    assert (
        conn.execute("SELECT COUNT(*) AS total FROM conversation_session_bindings").fetchone()[
            "total"
        ]
        == 0
    )
    conn.close()


def test_stored_selection_drives_resolution_first_cas_and_registered_availability(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "selected-backend.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_probe", employee_backend="probe-backend")
    conn.close()
    catalog = _catalog("hermes", "probe-backend")
    repository = SqliteConversationBindingRepository(
        str(db_path),
        workspace_root=tmp_path,
        integer_now=lambda: 30,
        employee_backend_catalog=catalog,
    )

    employee = asyncio.run(repository.resolve_employee("t_probe"))
    assert employee.backend_key == "probe-backend"
    assert repository.is_backend_available("hermes") is True
    assert repository.is_backend_available("probe-backend") is True
    assert repository.is_backend_available("missing") is False

    wrong = ConversationSessionBinding(
        employee_id="t_probe",
        acp_session_id="wrong-session",
        backend_key="hermes",
        binding_generation=1,
    )
    with pytest.raises(ConversationBindingError, match="does not match"):
        asyncio.run(repository.compare_and_swap(None, wrong))
    selected = wrong.model_copy(
        update={"acp_session_id": "probe-session", "backend_key": "probe-backend"}
    )
    assert asyncio.run(repository.compare_and_swap(None, selected)) == selected


def test_unregistered_storage_and_existing_binding_selection_mismatch_fail_closed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "backend-mismatch.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_probe", employee_backend="probe-backend")
    _insert_ticket(conn, "t_unknown", employee_backend="unknown-backend")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path),
        workspace_root=tmp_path,
        integer_now=lambda: 30,
        employee_backend_catalog=_catalog("hermes", "probe-backend"),
    )
    binding = ConversationSessionBinding(
        employee_id="t_probe",
        acp_session_id="probe-session",
        backend_key="probe-backend",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, binding))

    conn = connect(str(db_path))
    conn.execute("UPDATE tickets SET employee_backend = 'hermes' WHERE id = 't_probe'")
    conn.close()
    with pytest.raises(ConversationBindingError, match="does not match"):
        asyncio.run(repository.resolve("t_probe"))
    with pytest.raises(ConversationBindingError, match="not registered"):
        asyncio.run(repository.resolve_employee("t_unknown"))


def test_binding_cas_updates_ticket_mirror_atomically(tmp_path: Path) -> None:
    db_path = tmp_path / "bindings.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )

    assert asyncio.run(repository.compare_and_swap(None, first)) == first
    assert asyncio.run(repository.resolve_compaction_boundaries(first)) == ()
    check = connect(str(db_path))
    try:
        assert (
            check.execute("SELECT employee_session_id FROM tickets WHERE id='t_alpha'").fetchone()[
                0
            ]
            == "session-1"
        )
        row = check.execute(
            "SELECT entity_kind, entity_id, acp_session_id, binding_generation, "
            "compaction_boundaries_json "
            "FROM conversation_session_bindings WHERE employee_id='t_alpha'"
        ).fetchone()
        assert tuple(row) == ("ticket", "t_alpha", "session-1", 1, "[]")
    finally:
        check.close()


def test_unbound_conversation_generation_is_durable_without_an_acp_session(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "empty-conversation.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_empty", employee_backend="codex")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path),
        workspace_root=tmp_path,
        integer_now=lambda: 30,
        employee_backend_catalog=_catalog("codex"),
        chief_backend_key="codex",
    )

    conversation = asyncio.run(repository.ensure_conversation("t_empty"))

    assert conversation.employee_id == "t_empty"
    assert conversation.backend_key == "codex"
    assert conversation.conversation_generation == 1
    assert asyncio.run(repository.resolve("t_empty")) is None
    check = connect(str(db_path))
    try:
        assert check.execute(
            "SELECT employee_session_id FROM tickets WHERE id = 't_empty'"
        ).fetchone()[0] is None
        assert check.execute(
            "SELECT COUNT(*) FROM conversation_session_bindings WHERE employee_id = 't_empty'"
        ).fetchone()[0] == 0
    finally:
        check.close()


def test_new_conversation_clears_binding_and_first_later_binding_uses_its_generation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "new-empty-conversation.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_empty")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_empty",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, first))

    empty = asyncio.run(repository.start_new_conversation("t_empty", first))

    assert empty.conversation_generation == 2
    assert asyncio.run(repository.resolve("t_empty")) is None
    second = first.model_copy(
        update={"acp_session_id": "session-2", "binding_generation": 2}
    )
    assert asyncio.run(repository.compare_and_swap(None, second)) == second
    assert asyncio.run(repository.resolve("t_empty")) == second


def test_binding_cas_returns_loser_and_commits_exact_successor(tmp_path: Path) -> None:
    db_path = tmp_path / "cas.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, first))
    stale_candidate = first.model_copy(
        update={"acp_session_id": "session-stale", "binding_generation": 2}
    )
    unrelated_expected = first.model_copy(update={"acp_session_id": "session-other"})
    assert asyncio.run(repository.compare_and_swap(unrelated_expected, stale_candidate)) == first
    successor = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    assert asyncio.run(repository.compare_and_swap(first, successor)) == successor
    assert asyncio.run(repository.resolve("t_alpha")) == successor


def test_compaction_cas_commits_current_capture_provenance_atomically(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "compaction-cas.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    successor = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    compacted_boundaries = (
        ConversationCompactionBoundaryProvenance(
            boundary_id="boundary-explicit", trigger="explicit"
        ),
        ConversationCompactionBoundaryProvenance(
            boundary_id="boundary-automatic", trigger="automatic"
        ),
    )
    asyncio.run(repository.compare_and_swap(None, first))

    assert (
        asyncio.run(
            repository.compare_and_swap_compaction(
                first,
                (),
                successor,
                compacted_boundaries,
            )
        )
        == successor
    )
    assert asyncio.run(repository.resolve_compaction_boundaries(successor)) == compacted_boundaries
    check = connect(str(db_path))
    try:
        assert (
            check.execute(
                "SELECT employee_session_id FROM tickets WHERE id = 't_alpha'"
            ).fetchone()[0]
            == "session-2"
        )
        assert check.execute(
            "SELECT compaction_boundaries_json FROM conversation_session_bindings "
            "WHERE employee_id = 't_alpha'"
        ).fetchone()[0] == (
            '[{"boundary_id":"boundary-explicit","trigger":"explicit"},'
            '{"boundary_id":"boundary-automatic","trigger":"automatic"}]'
        )
    finally:
        check.close()


def test_later_compaction_replaces_prior_binding_provenance(tmp_path: Path) -> None:
    db_path = tmp_path / "successive-compactions.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    second = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    third = second.model_copy(update={"acp_session_id": "session-3", "binding_generation": 3})
    first_capture = (
        ConversationCompactionBoundaryProvenance(boundary_id="boundary-first", trigger="explicit"),
    )
    second_capture = (
        ConversationCompactionBoundaryProvenance(
            boundary_id="boundary-second", trigger="automatic"
        ),
    )
    asyncio.run(repository.compare_and_swap(None, first))
    asyncio.run(repository.compare_and_swap_compaction(first, (), second, first_capture))

    assert (
        asyncio.run(
            repository.compare_and_swap_compaction(second, first_capture, third, second_capture)
        )
        == third
    )
    assert asyncio.run(repository.resolve_compaction_boundaries(third)) == second_capture


def test_new_conversation_cas_replaces_prior_provenance_with_empty_tuple(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "new-conversation.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    second = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    replacement = second.model_copy(
        update={"acp_session_id": "session-new", "binding_generation": 3}
    )
    compacted_boundaries = (
        ConversationCompactionBoundaryProvenance(boundary_id="boundary-old", trigger="explicit"),
    )
    asyncio.run(repository.compare_and_swap(None, first))
    asyncio.run(repository.compare_and_swap_compaction(first, (), second, compacted_boundaries))

    assert asyncio.run(repository.compare_and_swap(second, replacement)) == replacement
    assert asyncio.run(repository.resolve_compaction_boundaries(replacement)) == ()


def test_compaction_cas_stale_expected_provenance_loses_without_writing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "stale-provenance.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    second = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    third = second.model_copy(update={"acp_session_id": "session-loser", "binding_generation": 3})
    durable_boundaries = (
        ConversationCompactionBoundaryProvenance(
            boundary_id="boundary-durable", trigger="explicit"
        ),
    )
    losing_boundaries = (
        ConversationCompactionBoundaryProvenance(boundary_id="boundary-loser", trigger="automatic"),
    )
    asyncio.run(repository.compare_and_swap(None, first))
    asyncio.run(repository.compare_and_swap_compaction(first, (), second, durable_boundaries))

    assert (
        asyncio.run(
            repository.compare_and_swap_compaction(
                second,
                (),
                third,
                losing_boundaries,
            )
        )
        == second
    )
    assert asyncio.run(repository.resolve_compaction_boundaries(second)) == durable_boundaries
    check = connect(str(db_path))
    try:
        assert (
            check.execute(
                "SELECT employee_session_id FROM tickets WHERE id = 't_alpha'"
            ).fetchone()[0]
            == "session-2"
        )
    finally:
        check.close()


def test_compaction_cas_binding_loss_retains_only_durable_winner_tuple(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "binding-loss.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    durable_winner = first.model_copy(
        update={"acp_session_id": "session-winner", "binding_generation": 2}
    )
    losing_candidate = first.model_copy(
        update={"acp_session_id": "session-loser", "binding_generation": 2}
    )
    durable_boundaries = (
        ConversationCompactionBoundaryProvenance(
            boundary_id="boundary-winner", trigger="automatic"
        ),
    )
    losing_boundaries = (
        ConversationCompactionBoundaryProvenance(boundary_id="boundary-loser", trigger="explicit"),
    )
    asyncio.run(repository.compare_and_swap(None, first))
    asyncio.run(
        repository.compare_and_swap_compaction(first, (), durable_winner, durable_boundaries)
    )

    assert (
        asyncio.run(
            repository.compare_and_swap_compaction(first, (), losing_candidate, losing_boundaries)
        )
        == durable_winner
    )
    assert (
        asyncio.run(repository.resolve_compaction_boundaries(durable_winner)) == durable_boundaries
    )


def test_compaction_cas_rejects_empty_current_capture_without_writing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "empty-compaction-capture.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    successor = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    asyncio.run(repository.compare_and_swap(None, first))

    with pytest.raises(ConversationBindingError, match="must not be empty"):
        asyncio.run(repository.compare_and_swap_compaction(first, (), successor, ()))

    assert asyncio.run(repository.resolve("t_alpha")) == first
    assert asyncio.run(repository.resolve_compaction_boundaries(first)) == ()


@pytest.mark.parametrize(
    "encoded",
    (
        "{",
        "{}",
        '[{"boundary_id":"boundary-only"}]',
        '[{"boundary_id":"boundary-extra","trigger":"explicit","summary":"no"}]',
    ),
)
def test_exact_provenance_read_rejects_malformed_persisted_json(
    tmp_path: Path,
    encoded: str,
) -> None:
    db_path = tmp_path / "malformed-provenance.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    binding = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, binding))
    corrupt = connect(str(db_path))
    corrupt.execute(
        "UPDATE conversation_session_bindings SET compaction_boundaries_json = ? "
        "WHERE employee_id = 't_alpha'",
        (encoded,),
    )
    corrupt.close()

    with pytest.raises(ConversationBindingError, match="compaction provenance"):
        asyncio.run(repository.resolve_compaction_boundaries(binding))


def test_exact_provenance_read_rejects_duplicate_persisted_boundary_ids(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "duplicate-provenance.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    binding = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, binding))
    corrupt = connect(str(db_path))
    corrupt.execute(
        "UPDATE conversation_session_bindings SET compaction_boundaries_json = ? "
        "WHERE employee_id = 't_alpha'",
        (
            '[{"boundary_id":"duplicate","trigger":"explicit"},'
            '{"boundary_id":"duplicate","trigger":"automatic"}]',
        ),
    )
    corrupt.close()

    with pytest.raises(ConversationBindingError, match="IDs must be unique"):
        asyncio.run(repository.resolve_compaction_boundaries(binding))


def test_exact_provenance_read_rejects_invalid_persisted_trigger(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "invalid-trigger.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    binding = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, binding))
    corrupt = connect(str(db_path))
    corrupt.execute(
        "UPDATE conversation_session_bindings SET compaction_boundaries_json = ? "
        "WHERE employee_id = 't_alpha'",
        ('[{"boundary_id":"boundary","trigger":"manual"}]',),
    )
    corrupt.close()

    with pytest.raises(ConversationBindingError, match="item is invalid"):
        asyncio.run(repository.resolve_compaction_boundaries(binding))


def test_compaction_cas_rejects_duplicate_current_boundary_ids_without_writing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "duplicate-current-capture.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    successor = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    duplicate_boundaries = (
        ConversationCompactionBoundaryProvenance(boundary_id="duplicate", trigger="explicit"),
        ConversationCompactionBoundaryProvenance(boundary_id="duplicate", trigger="automatic"),
    )
    asyncio.run(repository.compare_and_swap(None, first))

    with pytest.raises(ConversationBindingError, match="IDs must be unique"):
        asyncio.run(
            repository.compare_and_swap_compaction(first, (), successor, duplicate_boundaries)
        )

    assert asyncio.run(repository.resolve("t_alpha")) == first
    assert asyncio.run(repository.resolve_compaction_boundaries(first)) == ()


def test_compaction_cas_rejects_invalid_current_trigger_without_writing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "invalid-current-trigger.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    successor = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    invalid_boundary = ConversationCompactionBoundaryProvenance.model_construct(
        boundary_id="boundary-invalid",
        trigger=cast(ContextCompactionTrigger, "manual"),
    )
    asyncio.run(repository.compare_and_swap(None, first))

    with pytest.raises(ConversationBindingError, match="item is invalid"):
        asyncio.run(
            repository.compare_and_swap_compaction(first, (), successor, (invalid_boundary,))
        )

    assert asyncio.run(repository.resolve("t_alpha")) == first
    assert asyncio.run(repository.resolve_compaction_boundaries(first)) == ()


@pytest.mark.parametrize(
    "binding_changes",
    (
        {"employee_id": "t_other"},
        {"acp_session_id": "session-other"},
        {"backend_key": "backend-other"},
        {"binding_generation": 2},
    ),
)
def test_exact_provenance_read_rejects_any_binding_identity_mismatch(
    tmp_path: Path,
    binding_changes: dict[str, object],
) -> None:
    db_path = tmp_path / "exact-binding.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    binding = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    asyncio.run(repository.compare_and_swap(None, binding))
    mismatched = binding.model_copy(update=binding_changes)

    with pytest.raises(ConversationBindingError, match="exact binding changed"):
        asyncio.run(repository.resolve_compaction_boundaries(mismatched))


def test_in_memory_binding_repository_models_atomic_provenance_semantics() -> None:
    repository = InMemoryAcpBindingRepository()
    first = ConversationSessionBinding(
        employee_id="t_alpha",
        acp_session_id="session-1",
        backend_key="hermes",
        binding_generation=1,
    )
    second = first.model_copy(update={"acp_session_id": "session-2", "binding_generation": 2})
    losing_third = second.model_copy(
        update={"acp_session_id": "session-loser", "binding_generation": 3}
    )
    durable_boundaries = (
        ConversationCompactionBoundaryProvenance(
            boundary_id="boundary-durable", trigger="explicit"
        ),
    )
    losing_boundaries = (
        ConversationCompactionBoundaryProvenance(boundary_id="boundary-loser", trigger="automatic"),
    )

    assert asyncio.run(repository.compare_and_swap(None, first)) == first
    assert asyncio.run(repository.resolve_compaction_boundaries(first)) == ()
    assert (
        asyncio.run(repository.compare_and_swap_compaction(first, (), second, durable_boundaries))
        == second
    )
    assert (
        asyncio.run(
            repository.compare_and_swap_compaction(second, (), losing_third, losing_boundaries)
        )
        == second
    )
    assert asyncio.run(repository.resolve_compaction_boundaries(second)) == durable_boundaries


def test_employee_resolver_accepts_only_ticket_and_exact_chief(tmp_path: Path) -> None:
    db_path = tmp_path / "resolve.db"
    conn = connect(str(db_path))
    create_schema(conn)
    _insert_ticket(conn, "t_alpha")
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )

    ticket = asyncio.run(repository.resolve_employee("t_alpha"))
    chief = asyncio.run(repository.resolve_employee(CHIEF_OF_STAFF_ENTITY_ID))
    assert (ticket.entity_kind, ticket.entity_id, ticket.backend_key) == (
        "ticket",
        "t_alpha",
        "hermes",
    )
    assert (chief.entity_kind, chief.entity_id, chief.backend_key) == (
        "agent",
        CHIEF_OF_STAFF_ENTITY_ID,
        "hermes",
    )
    for invalid in ("day_2026-07-20", "agent_unknown", "t_missing"):
        with pytest.raises(ValueError):
            asyncio.run(repository.resolve_employee(invalid))


def test_chief_first_binding_has_no_second_product_mirror(tmp_path: Path) -> None:
    db_path = tmp_path / "chief-binding.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    repository = SqliteConversationBindingRepository(
        str(db_path), workspace_root=tmp_path, integer_now=lambda: 30
    )
    candidate = ConversationSessionBinding(
        employee_id=CHIEF_OF_STAFF_ENTITY_ID,
        acp_session_id="chief-session",
        backend_key="hermes",
        binding_generation=1,
    )
    assert asyncio.run(repository.compare_and_swap(None, candidate)) == candidate
    check = connect(str(db_path))
    assert tuple(
        check.execute(
            "SELECT entity_kind, entity_id, acp_session_id, binding_generation "
            "FROM conversation_session_bindings WHERE employee_id = ?",
            (CHIEF_OF_STAFF_ENTITY_ID,),
        ).fetchone()
    ) == ("agent", CHIEF_OF_STAFF_ENTITY_ID, "chief-session", 1)
    assert "agent_chat_sessions" not in {
        str(row["name"])
        for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def test_new_chief_conversation_owns_launch_snapshot_until_first_binding(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "chief-empty-snapshot.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    registry = configured_worker_type_registry()
    worker_settings_service.update_chief_launch_defaults(
        tmp_path,
        {
            "employee_backend": "codex",
            "employee_launch_model": "gpt-5.6-sol",
            "employee_launch_reasoning_effort": "medium",
        },
    )
    repository = _SqliteConversationBindingRepository(
        str(db_path),
        workspace_root=tmp_path,
        integer_now=lambda: 30,
        busy_timeout_ms=5000,
        employee_backend_catalog=build_production_employee_backend_catalog(),
        chief_backend_key="codex",
        worker_type_registry=registry,
    )

    empty = asyncio.run(repository.start_new_conversation(CHIEF_OF_STAFF_ENTITY_ID, None))
    assert (
        empty.backend_key,
        empty.employee_launch_model,
        empty.employee_launch_reasoning_effort,
        empty.conversation_generation,
    ) == ("codex", "gpt-5.6-sol", "medium", 2)

    worker_settings_service.update_chief_launch_defaults(
        tmp_path,
        {
            "employee_backend": "codex",
            "employee_launch_model": "gpt-5.6-sol-new",
            "employee_launch_reasoning_effort": "high",
        },
    )
    employee = asyncio.run(repository.resolve_employee(CHIEF_OF_STAFF_ENTITY_ID))
    assert (
        employee.backend_key,
        employee.employee_launch_model,
        employee.employee_launch_reasoning_effort,
    ) == ("codex", "gpt-5.6-sol", "medium")
    candidate = ConversationSessionBinding(
        employee_id=CHIEF_OF_STAFF_ENTITY_ID,
        acp_session_id="chief-session",
        backend_key="codex",
        employee_launch_model="gpt-5.6-sol",
        employee_launch_reasoning_effort="medium",
        binding_generation=2,
    )
    prepared = EmployeeLaunchConfiguration("codex", "gpt-5.6-sol", "medium")

    assert asyncio.run(repository.compare_and_swap_initial(candidate, prepared)) == candidate


def test_chief_settings_save_and_initial_binding_share_settings_then_sqlite_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "chief-lock-order.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    registry = configured_worker_type_registry()
    repository = _SqliteConversationBindingRepository(
        str(db_path),
        workspace_root=tmp_path,
        integer_now=lambda: 30,
        busy_timeout_ms=250,
        employee_backend_catalog=build_production_employee_backend_catalog(),
        chief_backend_key="codex",
        worker_type_registry=registry,
    )
    candidate = ConversationSessionBinding(
        employee_id=CHIEF_OF_STAFF_ENTITY_ID,
        acp_session_id="chief-session",
        backend_key="codex",
        employee_launch_model="gpt-5.6-sol",
        employee_launch_reasoning_effort="medium",
        binding_generation=1,
    )
    prepared = EmployeeLaunchConfiguration("codex", "gpt-5.6-sol", "medium")
    settings_publish_started = threading.Event()
    binding_called = threading.Event()
    binding_began_sqlite = threading.Event()
    original_repository_connect = binding_repository_module.connect

    def observed_repository_connect(path: str, busy_timeout_ms: int = 5000):
        observed = original_repository_connect(path, busy_timeout_ms)
        observed.set_trace_callback(
            lambda statement: binding_began_sqlite.set() if statement == "BEGIN IMMEDIATE" else None
        )
        return observed

    monkeypatch.setattr(binding_repository_module, "connect", observed_repository_connect)

    def publish_sqlite_event() -> None:
        settings_publish_started.set()
        assert binding_called.wait(timeout=2)
        assert not binding_began_sqlite.wait(timeout=0.1)
        event_conn = connect(str(db_path), 250)
        try:
            event_conn.execute("BEGIN IMMEDIATE")
            event_conn.execute("COMMIT")
        finally:
            event_conn.close()

    def save_settings() -> None:
        worker_settings_service.update_chief_launch_defaults(
            tmp_path,
            {
                "employee_backend": "codex",
                "employee_launch_model": "gpt-5.6-sol-new",
                "employee_launch_reasoning_effort": "high",
            },
            after_publish=publish_sqlite_event,
        )

    def bind() -> None:
        binding_called.set()
        with pytest.raises(
            ConversationBindingError,
            match="Chief employee configuration changed before binding",
        ):
            asyncio.run(repository.compare_and_swap_initial(candidate, prepared))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        save = executor.submit(save_settings)
        assert settings_publish_started.wait(timeout=2)
        binding = executor.submit(bind)
        save.result(timeout=2)
        binding.result(timeout=2)

    assert binding_began_sqlite.is_set()
