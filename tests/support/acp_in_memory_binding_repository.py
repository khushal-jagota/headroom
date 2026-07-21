"""Locked in-memory durable-binding seam for ACP runtime tests."""

from __future__ import annotations

import asyncio

from planner.conversation.contracts import (
    ConversationCompactionBoundaryProvenance,
    ConversationSessionBinding,
)


class InMemoryAcpBindingRepository:
    def __init__(self) -> None:
        self._bindings: dict[str, ConversationSessionBinding] = {}
        self._compaction_boundaries: dict[
            str, tuple[ConversationCompactionBoundaryProvenance, ...]
        ] = {}
        self._lock = asyncio.Lock()

    async def resolve(self, employee_id: str) -> ConversationSessionBinding | None:
        async with self._lock:
            return self._bindings.get(employee_id)

    async def resolve_compaction_boundaries(
        self, binding: ConversationSessionBinding
    ) -> tuple[ConversationCompactionBoundaryProvenance, ...]:
        async with self._lock:
            if self._bindings.get(binding.employee_id) != binding:
                raise RuntimeError(
                    "exact binding changed before compaction provenance read"
                )
            return self._compaction_boundaries[binding.employee_id]

    async def compare_and_swap(
        self,
        expected: ConversationSessionBinding | None,
        candidate: ConversationSessionBinding,
    ) -> ConversationSessionBinding:
        if candidate.employee_id != (
            expected.employee_id if expected is not None else candidate.employee_id
        ):
            raise ValueError("binding CAS employee identity mismatch")
        async with self._lock:
            actual = self._bindings.get(candidate.employee_id)
            if actual != expected:
                if actual is None:
                    raise RuntimeError("binding disappeared during compare-and-swap")
                return actual
            if expected is None and candidate.binding_generation != 1:
                raise ValueError("first binding generation must be one")
            if expected is not None and (
                candidate.binding_generation != expected.binding_generation + 1
            ):
                raise ValueError("replacement binding generation must be the exact successor")
            self._bindings[candidate.employee_id] = candidate
            self._compaction_boundaries[candidate.employee_id] = ()
            return candidate

    async def compare_and_swap_compaction(
        self,
        expected: ConversationSessionBinding,
        expected_compaction_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
        candidate: ConversationSessionBinding,
        candidate_compaction_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ],
    ) -> ConversationSessionBinding:
        if expected.employee_id != candidate.employee_id:
            raise ValueError("binding CAS employee identity mismatch")
        if not candidate_compaction_boundaries:
            raise ValueError("compaction candidate provenance must not be empty")
        validated_expected = self._validate_compaction_boundaries(
            expected_compaction_boundaries
        )
        validated_candidate = self._validate_compaction_boundaries(
            candidate_compaction_boundaries
        )
        async with self._lock:
            actual = self._bindings.get(candidate.employee_id)
            actual_boundaries = self._compaction_boundaries.get(candidate.employee_id, ())
            if actual != expected or actual_boundaries != validated_expected:
                if actual is None:
                    raise RuntimeError("binding disappeared during compare-and-swap")
                return actual
            if candidate.binding_generation != expected.binding_generation + 1:
                raise ValueError("replacement binding generation must be the exact successor")
            self._bindings[candidate.employee_id] = candidate
            self._compaction_boundaries[candidate.employee_id] = validated_candidate
            return candidate

    async def seed(
        self,
        binding: ConversationSessionBinding,
        compaction_boundaries: tuple[
            ConversationCompactionBoundaryProvenance, ...
        ] = (),
    ) -> None:
        validated = self._validate_compaction_boundaries(compaction_boundaries)
        async with self._lock:
            self._bindings[binding.employee_id] = binding
            self._compaction_boundaries[binding.employee_id] = validated

    @staticmethod
    def _validate_compaction_boundaries(
        boundaries: tuple[ConversationCompactionBoundaryProvenance, ...],
    ) -> tuple[ConversationCompactionBoundaryProvenance, ...]:
        if not isinstance(boundaries, tuple):
            raise ValueError("binding compaction provenance must be an ordered tuple")
        validated: list[ConversationCompactionBoundaryProvenance] = []
        boundary_ids: set[str] = set()
        for boundary in boundaries:
            if not isinstance(boundary, ConversationCompactionBoundaryProvenance):
                raise ValueError("binding compaction provenance has an invalid item type")
            exact = ConversationCompactionBoundaryProvenance.model_validate(
                boundary.model_dump()
            )
            if exact.boundary_id in boundary_ids:
                raise ValueError(
                    "binding compaction provenance boundary IDs must be unique"
                )
            boundary_ids.add(exact.boundary_id)
            validated.append(exact)
        return tuple(validated)
