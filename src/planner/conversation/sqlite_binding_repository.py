"""SQLite ownership for durable ACP session bindings and employee resolution."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from planner.core.db import connect
from planner.tickets import data as tickets_data
from planner.tickets.contracts import EmployeeSessionIdTransition

from .backend_catalog import EmployeeBackendCatalog
from .contracts import (
    CHIEF_OF_STAFF_ENTITY_ID,
    ConversationCompactionBoundaryProvenance,
    ConversationEmployee,
    ConversationEntityKind,
    ConversationSessionBinding,
    parse_conversation_compaction_boundaries_json,
)


class ConversationBindingError(RuntimeError):
    pass


class SqliteConversationBindingRepository:
    """One short-lived connection per read and one immediate transaction per CAS."""

    def __init__(
        self,
        db_path: str,
        *,
        workspace_root: Path,
        integer_now: Callable[[], int],
        busy_timeout_ms: int = 5000,
        employee_backend_catalog: EmployeeBackendCatalog,
        chief_backend_key: str,
    ) -> None:
        if not workspace_root.is_absolute():
            raise ValueError("workspace_root must be absolute")
        self._db_path = db_path
        self._workspace_root = workspace_root
        self._integer_now = integer_now
        self._busy_timeout_ms = busy_timeout_ms
        if not employee_backend_catalog.is_registered(chief_backend_key):
            raise ValueError("Chief backend must be registered")
        self._employee_backend_catalog = employee_backend_catalog
        self._chief_backend_key = chief_backend_key

    async def resolve(self, employee_id: str) -> ConversationSessionBinding | None:
        return await asyncio.to_thread(self._resolve_sync, employee_id)

    async def resolve_employee(self, employee_id: str) -> ConversationEmployee:
        return await asyncio.to_thread(self._resolve_employee_sync, employee_id)

    async def resolve_compaction_boundaries(
        self, binding: ConversationSessionBinding
    ) -> tuple[ConversationCompactionBoundaryProvenance, ...]:
        return await asyncio.to_thread(self._resolve_compaction_boundaries_sync, binding)

    async def compare_and_swap(
        self,
        expected: ConversationSessionBinding | None,
        candidate: ConversationSessionBinding,
    ) -> ConversationSessionBinding:
        return await asyncio.to_thread(
            self._compare_and_swap_sync,
            expected,
            candidate,
            None,
            (),
        )

    async def compare_and_swap_compaction(
        self,
        expected: ConversationSessionBinding,
        expected_compaction_boundaries: tuple[ConversationCompactionBoundaryProvenance, ...],
        candidate: ConversationSessionBinding,
        candidate_compaction_boundaries: tuple[ConversationCompactionBoundaryProvenance, ...],
    ) -> ConversationSessionBinding:
        if not candidate_compaction_boundaries:
            raise ConversationBindingError("compaction candidate provenance must not be empty")
        return await asyncio.to_thread(
            self._compare_and_swap_sync,
            expected,
            candidate,
            expected_compaction_boundaries,
            candidate_compaction_boundaries,
        )

    def is_backend_available(self, backend_key: str) -> bool:
        return self._employee_backend_catalog.is_registered(backend_key)

    def _resolve_sync(self, employee_id: str) -> ConversationSessionBinding | None:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            return self._read_validated_binding(conn, employee_id)
        finally:
            conn.close()

    def _resolve_employee_sync(self, employee_id: str) -> ConversationEmployee:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            entity_kind, _mirror, selected_backend = self._classify_and_read_mirror(
                conn, employee_id
            )
            binding = self._read_validated_binding(conn, employee_id)
            backend_key = binding.backend_key if binding is not None else selected_backend
            return ConversationEmployee(
                employee_id=employee_id,
                entity_kind=entity_kind,
                entity_id=employee_id,
                workspace_roots=(self._workspace_root,),
                backend_key=backend_key,
            )
        finally:
            conn.close()

    def _resolve_compaction_boundaries_sync(
        self, binding: ConversationSessionBinding
    ) -> tuple[ConversationCompactionBoundaryProvenance, ...]:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            actual, compacted_boundaries = self._read_validated_binding_row(
                conn, binding.employee_id
            )
            if actual != binding:
                raise ConversationBindingError(
                    "exact binding changed before compaction provenance read"
                )
            return compacted_boundaries
        finally:
            conn.close()

    def _compare_and_swap_sync(
        self,
        expected: ConversationSessionBinding | None,
        candidate: ConversationSessionBinding,
        expected_compaction_boundaries: tuple[ConversationCompactionBoundaryProvenance, ...] | None,
        candidate_compaction_boundaries: tuple[ConversationCompactionBoundaryProvenance, ...],
    ) -> ConversationSessionBinding:
        if expected is not None and expected.employee_id != candidate.employee_id:
            raise ConversationBindingError("binding CAS employee identity mismatch")
        if expected is None and expected_compaction_boundaries is not None:
            raise ConversationBindingError("compaction CAS requires an existing expected binding")
        validated_expected_boundaries = (
            None
            if expected_compaction_boundaries is None
            else self._validate_compaction_boundaries(expected_compaction_boundaries)
        )
        validated_candidate_boundaries = self._validate_compaction_boundaries(
            candidate_compaction_boundaries
        )
        encoded_candidate_boundaries = self._serialize_compaction_boundaries(
            validated_candidate_boundaries
        )
        conn = connect(self._db_path, self._busy_timeout_ms)
        conn.execute("BEGIN IMMEDIATE")
        try:
            entity_kind, mirror_session, selected_backend = self._classify_and_read_mirror(
                conn, candidate.employee_id
            )
            self._require_registered_backend(candidate.backend_key)
            if candidate.backend_key != selected_backend:
                raise ConversationBindingError(
                    "binding candidate does not match the employee backend selection"
                )
            actual, actual_compaction_boundaries = self._read_validated_binding_row(
                conn, candidate.employee_id
            )
            if actual != expected or (
                validated_expected_boundaries is not None
                and actual_compaction_boundaries != validated_expected_boundaries
            ):
                if actual is None:
                    raise ConversationBindingError(
                        "durable binding disappeared during compare-and-swap"
                    )
                if actual.backend_key != selected_backend:
                    raise ConversationBindingError(
                        "durable binding winner does not match the employee backend selection"
                    )
                conn.execute("COMMIT")
                return actual
            if expected is None:
                if candidate.binding_generation != 1:
                    raise ConversationBindingError("first binding generation must be one")
                if entity_kind == "ticket" and mirror_session is not None:
                    raise ConversationBindingError(
                        "unbound employee already has a different product mirror"
                    )
            else:
                if candidate.backend_key != expected.backend_key:
                    raise ConversationBindingError("replacement binding must preserve its backend")
                if candidate.binding_generation != expected.binding_generation + 1:
                    raise ConversationBindingError(
                        "replacement binding generation must be the exact successor"
                    )
                if entity_kind == "ticket" and mirror_session != expected.acp_session_id:
                    raise ConversationBindingError(
                        "product mirror changed before binding replacement"
                    )
            now = self._integer_now()
            if entity_kind == "ticket":
                effective = tickets_data.write_employee_session_id_in_transaction(
                    conn,
                    candidate.employee_id,
                    transition=EmployeeSessionIdTransition(
                        expected_employee_session_id=mirror_session,
                        candidate_employee_session_id=candidate.acp_session_id,
                    ),
                    force_fresh_employee_session=False,
                    now=now,
                )
            else:
                effective = candidate.acp_session_id
            if effective != candidate.acp_session_id:
                raise ConversationBindingError(
                    "product mirror retained a different conversation winner"
                )
            if expected is None:
                conn.execute(
                    "INSERT INTO conversation_session_bindings "
                    "(employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
                    "binding_generation, compaction_boundaries_json, created_at, "
                    "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        candidate.employee_id,
                        entity_kind,
                        candidate.employee_id,
                        candidate.acp_session_id,
                        candidate.backend_key,
                        candidate.binding_generation,
                        encoded_candidate_boundaries,
                        now,
                        now,
                    ),
                )
            else:
                cursor = conn.execute(
                    "UPDATE conversation_session_bindings SET acp_session_id = ?, "
                    "backend_key = ?, binding_generation = ?, "
                    "compaction_boundaries_json = ?, updated_at = ? "
                    "WHERE employee_id = ? AND entity_kind = ? AND entity_id = ? "
                    "AND acp_session_id = ? AND backend_key = ? AND binding_generation = ?",
                    (
                        candidate.acp_session_id,
                        candidate.backend_key,
                        candidate.binding_generation,
                        encoded_candidate_boundaries,
                        now,
                        candidate.employee_id,
                        entity_kind,
                        candidate.employee_id,
                        expected.acp_session_id,
                        expected.backend_key,
                        expected.binding_generation,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConversationBindingError(
                        "complete binding changed during compare-and-swap"
                    )
            committed, committed_compaction_boundaries = self._read_validated_binding_row(
                conn, candidate.employee_id
            )
            if (
                committed != candidate
                or committed_compaction_boundaries != validated_candidate_boundaries
            ):
                raise ConversationBindingError("binding candidate did not commit exactly")
            conn.execute("COMMIT")
            return candidate
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def _read_validated_binding(
        self, conn: sqlite3.Connection, employee_id: str
    ) -> ConversationSessionBinding | None:
        binding, _compacted_boundaries = self._read_validated_binding_row(conn, employee_id)
        return binding

    def _read_validated_binding_row(
        self, conn: sqlite3.Connection, employee_id: str
    ) -> tuple[
        ConversationSessionBinding | None,
        tuple[ConversationCompactionBoundaryProvenance, ...],
    ]:
        row = conn.execute(
            "SELECT employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
            "binding_generation, compaction_boundaries_json "
            "FROM conversation_session_bindings WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        if row is None:
            return None, ()
        entity_kind, mirror_session, selected_backend = self._classify_and_read_mirror(
            conn, employee_id
        )
        if str(row["entity_kind"]) != entity_kind or str(row["entity_id"]) != employee_id:
            raise ConversationBindingError("binding entity metadata is inconsistent")
        if entity_kind == "ticket" and row["acp_session_id"] != mirror_session:
            raise ConversationBindingError("binding does not match its product mirror")
        binding = ConversationSessionBinding(
            employee_id=str(row["employee_id"]),
            acp_session_id=str(row["acp_session_id"]),
            backend_key=str(row["backend_key"]),
            binding_generation=int(row["binding_generation"]),
        )
        if binding.employee_id != employee_id:
            raise ConversationBindingError("binding belongs to another employee")
        self._require_registered_backend(binding.backend_key)
        if binding.backend_key != selected_backend:
            raise ConversationBindingError("binding does not match the employee backend selection")
        compacted_boundaries = self._parse_compaction_boundaries(row["compaction_boundaries_json"])
        return binding, compacted_boundaries

    @staticmethod
    def _parse_compaction_boundaries(
        encoded: object,
    ) -> tuple[ConversationCompactionBoundaryProvenance, ...]:
        try:
            return parse_conversation_compaction_boundaries_json(encoded)
        except ValueError as error:
            raise ConversationBindingError(f"binding {error}") from error

    @classmethod
    def _validate_compaction_boundaries(
        cls,
        boundaries: tuple[ConversationCompactionBoundaryProvenance, ...],
    ) -> tuple[ConversationCompactionBoundaryProvenance, ...]:
        if not isinstance(boundaries, tuple):
            raise ConversationBindingError("binding compaction provenance must be an ordered tuple")
        validated: list[ConversationCompactionBoundaryProvenance] = []
        boundary_ids: set[str] = set()
        for boundary in boundaries:
            if not isinstance(boundary, ConversationCompactionBoundaryProvenance):
                raise ConversationBindingError(
                    "binding compaction provenance has an invalid item type"
                )
            try:
                exact = ConversationCompactionBoundaryProvenance.model_validate(
                    boundary.model_dump()
                )
            except ValueError as error:
                raise ConversationBindingError(
                    "binding compaction provenance item is invalid"
                ) from error
            if exact.boundary_id in boundary_ids:
                raise ConversationBindingError(
                    "binding compaction provenance boundary IDs must be unique"
                )
            boundary_ids.add(exact.boundary_id)
            validated.append(exact)
        return tuple(validated)

    @staticmethod
    def _serialize_compaction_boundaries(
        boundaries: tuple[ConversationCompactionBoundaryProvenance, ...],
    ) -> str:
        return json.dumps(
            [boundary.model_dump() for boundary in boundaries],
            separators=(",", ":"),
        )

    def _classify_and_read_mirror(
        self, conn: sqlite3.Connection, employee_id: str
    ) -> tuple[ConversationEntityKind, str | None, str]:
        ticket = conn.execute(
            "SELECT employee_session_id, employee_backend FROM tickets WHERE id = ?",
            (employee_id,),
        ).fetchone()
        if ticket is not None:
            mirror = ticket["employee_session_id"]
            selected_backend = str(ticket["employee_backend"])
            self._require_registered_backend(selected_backend)
            return "ticket", None if mirror is None else str(mirror), selected_backend
        if employee_id == CHIEF_OF_STAFF_ENTITY_ID:
            return "agent", None, self._chief_backend_key
        raise ValueError("employee must be an existing Ticket or the Chief of Staff")

    def _require_registered_backend(self, backend_key: str) -> None:
        if not self._employee_backend_catalog.is_registered(backend_key):
            raise ConversationBindingError(f"employee backend {backend_key!r} is not registered")
