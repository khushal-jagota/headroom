"""Reusable ten-probe ACP conformance contract and mutation machinery."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, cast

MANIFEST_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "acp" / "conformance-manifest.json"
)


@dataclass(frozen=True, slots=True)
class AcpConformanceProbe:
    probe_id: str
    description: str
    production_owner: str


@dataclass(frozen=True, slots=True)
class LoadReplayEvidence:
    replay_callback_positions: tuple[int, ...]
    load_response_position: int


@dataclass(frozen=True, slots=True)
class ThoughtEvidence:
    live_update_types: tuple[str, ...]
    replay_update_types: tuple[str, ...]
    live_thought_chunks: tuple[str, ...]
    replay_thought_chunks: tuple[str, ...]
    live_assistant_outputs: tuple[str, ...]
    replay_assistant_outputs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MessageGroupingEvidence:
    stable_group_ids: tuple[str, ...]
    missing_id_live_group_ids: tuple[str, ...]
    missing_id_replay_group_ids: tuple[str, ...]
    boundary_reset_group_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlanToolEvidence:
    observed_plan_snapshots: tuple[tuple[str, ...], ...]
    final_plan: tuple[str, ...]
    tool_call_ids: tuple[str, ...]
    final_tool_status: str


@dataclass(frozen=True, slots=True)
class PermissionSettlementEvidence:
    settlements_by_cause: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class RefreshEvidence:
    employee_id: str
    original_session_id: str
    refreshed_session_id: str
    original_binding_generation: int
    refreshed_binding_generation: int


@dataclass(frozen=True, slots=True)
class DeliveryCapabilityEvidence:
    supports_steer: bool
    steer_result: str
    unsupported_steer_visible: bool
    broker_operations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompactionEvidence:
    explicit_states: tuple[str, ...]
    automatic_states: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CallbackOrderEvidence:
    wire_order: tuple[int, ...]
    reduced_order: tuple[int, ...]
    broadcast_order: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ProtocolRejectionEvidence:
    rejected_discriminators: tuple[str, ...]
    visible_statuses: tuple[str, ...]
    assistant_text_fallbacks: tuple[str, ...]


type ProbeEvidence = (
    LoadReplayEvidence
    | ThoughtEvidence
    | MessageGroupingEvidence
    | PlanToolEvidence
    | PermissionSettlementEvidence
    | RefreshEvidence
    | DeliveryCapabilityEvidence
    | CompactionEvidence
    | CallbackOrderEvidence
    | ProtocolRejectionEvidence
)


class AcpConformanceSubject(Protocol):
    def observe_load_replay(self) -> LoadReplayEvidence: ...

    def observe_typed_thought(self) -> ThoughtEvidence: ...

    def observe_message_grouping(self) -> MessageGroupingEvidence: ...

    def observe_plan_tool_reconciliation(self) -> PlanToolEvidence: ...

    def observe_permission_settlement(self) -> PermissionSettlementEvidence: ...

    def observe_refresh_binding(self) -> RefreshEvidence: ...

    def observe_delivery_capabilities(self) -> DeliveryCapabilityEvidence: ...

    def observe_compaction(self) -> CompactionEvidence: ...

    def observe_callback_order(self) -> CallbackOrderEvidence: ...

    def observe_protocol_rejection(self) -> ProtocolRejectionEvidence: ...


def load_conformance_manifest() -> tuple[AcpConformanceProbe, ...]:
    raw_manifest = json.loads(MANIFEST_PATH.read_text())
    return tuple(
        AcpConformanceProbe(
            probe_id=item["probeId"],
            description=item["description"],
            production_owner=item["productionOwner"],
        )
        for item in raw_manifest
    )


def assert_load_replay(evidence: LoadReplayEvidence) -> None:
    assert evidence.replay_callback_positions, "load must replay at least one typed callback"
    assert max(evidence.replay_callback_positions) < evidence.load_response_position


def assert_typed_thought(evidence: ThoughtEvidence) -> None:
    assert "agent_thought_chunk" in evidence.live_update_types
    assert "agent_thought_chunk" in evidence.replay_update_types
    assert evidence.live_thought_chunks
    assert evidence.replay_thought_chunks == evidence.live_thought_chunks
    assert evidence.replay_assistant_outputs == evidence.live_assistant_outputs
    for thought_chunks, assistant_outputs in (
        (evidence.live_thought_chunks, evidence.live_assistant_outputs),
        (evidence.replay_thought_chunks, evidence.replay_assistant_outputs),
    ):
        thought_forms = thought_chunks + ("".join(thought_chunks),)
        assert all(
            thought_form not in assistant_output
            for thought_form in thought_forms
            for assistant_output in assistant_outputs
        )


def assert_message_grouping(evidence: MessageGroupingEvidence) -> None:
    assert len(set(evidence.stable_group_ids)) == 1
    assert evidence.missing_id_live_group_ids == evidence.missing_id_replay_group_ids
    assert len(set(evidence.missing_id_live_group_ids)) == 1
    assert len(set(evidence.boundary_reset_group_ids)) == len(evidence.boundary_reset_group_ids)


def assert_plan_tool_reconciliation(evidence: PlanToolEvidence) -> None:
    assert len(evidence.observed_plan_snapshots) >= 2
    assert evidence.final_plan == evidence.observed_plan_snapshots[-1]
    assert evidence.final_plan == ("Replacement snapshot",)
    assert evidence.tool_call_ids == ("tool-1",)
    assert evidence.final_tool_status == "completed"


def assert_permission_exactly_once(evidence: PermissionSettlementEvidence) -> None:
    assert evidence.settlements_by_cause == (
        ("cancel", 1),
        ("death", 1),
        ("last_browser_disconnect", 1),
        ("timeout", 1),
    )


def assert_durable_refresh(evidence: RefreshEvidence) -> None:
    assert evidence.employee_id
    assert evidence.original_session_id == evidence.refreshed_session_id
    assert evidence.original_binding_generation == evidence.refreshed_binding_generation
    assert evidence.original_binding_generation > 0


def assert_delivery_capabilities(evidence: DeliveryCapabilityEvidence) -> None:
    if evidence.supports_steer:
        assert evidence.steer_result == "accepted_native"
    else:
        assert evidence.steer_result == "rejected_unavailable"
        assert evidence.unsupported_steer_visible
    assert evidence.broker_operations == ("queue", "send_now")


def assert_compaction_visibility(evidence: CompactionEvidence) -> None:
    assert evidence.explicit_states == ("compacting", "compacted")
    assert evidence.automatic_states == ("compacting", "compacted")


def assert_callback_wire_order(evidence: CallbackOrderEvidence) -> None:
    assert evidence.wire_order
    assert evidence.reduced_order == evidence.wire_order
    assert evidence.broadcast_order == evidence.wire_order


def assert_protocol_rejection(evidence: ProtocolRejectionEvidence) -> None:
    assert evidence.rejected_discriminators == ("future_update", "agent_thought_chunk")
    assert evidence.visible_statuses == (
        "Agent sent an unsupported update",
        "Agent sent an unsupported update",
    )
    assert evidence.assistant_text_fallbacks == ()


def evidence_for_probe(subject: AcpConformanceSubject, probe_id: str) -> ProbeEvidence:
    observations = {
        "load_replay_before_response": subject.observe_load_replay,
        "typed_thought": subject.observe_typed_thought,
        "message_grouping": subject.observe_message_grouping,
        "plan_tool_reconciliation": subject.observe_plan_tool_reconciliation,
        "permission_exactly_once": subject.observe_permission_settlement,
        "durable_refresh": subject.observe_refresh_binding,
        "delivery_capabilities": subject.observe_delivery_capabilities,
        "compaction_visibility": subject.observe_compaction,
        "callback_wire_order": subject.observe_callback_order,
        "protocol_rejection": subject.observe_protocol_rejection,
    }
    return observations[probe_id]()


def assert_probe(subject: AcpConformanceSubject, probe_id: str) -> None:
    assertions = {
        "load_replay_before_response": assert_load_replay,
        "typed_thought": assert_typed_thought,
        "message_grouping": assert_message_grouping,
        "plan_tool_reconciliation": assert_plan_tool_reconciliation,
        "permission_exactly_once": assert_permission_exactly_once,
        "durable_refresh": assert_durable_refresh,
        "delivery_capabilities": assert_delivery_capabilities,
        "compaction_visibility": assert_compaction_visibility,
        "callback_wire_order": assert_callback_wire_order,
        "protocol_rejection": assert_protocol_rejection,
    }
    assertions[probe_id](evidence_for_probe(subject, probe_id))  # type: ignore[arg-type]


def assert_all_probes(subject: AcpConformanceSubject) -> None:
    for probe in load_conformance_manifest():
        assert_probe(subject, probe.probe_id)


class MutatedAcpConformanceSubject:
    def __init__(self, base: AcpConformanceSubject, mutated_probe_id: str) -> None:
        self._base = base
        self._mutated_probe_id = mutated_probe_id

    def _maybe_mutate(self, probe_id: str, evidence: ProbeEvidence) -> ProbeEvidence:
        if probe_id != self._mutated_probe_id:
            return evidence
        return mutate_probe_evidence(probe_id, evidence)

    def observe_load_replay(self) -> LoadReplayEvidence:
        return cast(
            LoadReplayEvidence,
            self._maybe_mutate("load_replay_before_response", self._base.observe_load_replay()),
        )

    def observe_typed_thought(self) -> ThoughtEvidence:
        return cast(
            ThoughtEvidence,
            self._maybe_mutate("typed_thought", self._base.observe_typed_thought()),
        )

    def observe_message_grouping(self) -> MessageGroupingEvidence:
        return cast(
            MessageGroupingEvidence,
            self._maybe_mutate("message_grouping", self._base.observe_message_grouping()),
        )

    def observe_plan_tool_reconciliation(self) -> PlanToolEvidence:
        return cast(
            PlanToolEvidence,
            self._maybe_mutate(
                "plan_tool_reconciliation", self._base.observe_plan_tool_reconciliation()
            ),
        )

    def observe_permission_settlement(self) -> PermissionSettlementEvidence:
        return cast(
            PermissionSettlementEvidence,
            self._maybe_mutate(
                "permission_exactly_once", self._base.observe_permission_settlement()
            ),
        )

    def observe_refresh_binding(self) -> RefreshEvidence:
        return cast(
            RefreshEvidence,
            self._maybe_mutate("durable_refresh", self._base.observe_refresh_binding()),
        )

    def observe_delivery_capabilities(self) -> DeliveryCapabilityEvidence:
        return cast(
            DeliveryCapabilityEvidence,
            self._maybe_mutate(
                "delivery_capabilities", self._base.observe_delivery_capabilities()
            ),
        )

    def observe_compaction(self) -> CompactionEvidence:
        return cast(
            CompactionEvidence,
            self._maybe_mutate("compaction_visibility", self._base.observe_compaction()),
        )

    def observe_callback_order(self) -> CallbackOrderEvidence:
        return cast(
            CallbackOrderEvidence,
            self._maybe_mutate("callback_wire_order", self._base.observe_callback_order()),
        )

    def observe_protocol_rejection(self) -> ProtocolRejectionEvidence:
        return cast(
            ProtocolRejectionEvidence,
            self._maybe_mutate("protocol_rejection", self._base.observe_protocol_rejection()),
        )


def mutate_probe_evidence(probe_id: str, evidence: ProbeEvidence) -> ProbeEvidence:
    if probe_id == "load_replay_before_response":
        item = cast(LoadReplayEvidence, evidence)
        return replace(item, load_response_position=0)
    if probe_id == "typed_thought":
        item = cast(ThoughtEvidence, evidence)
        return replace(
            item,
            replay_assistant_outputs=item.replay_assistant_outputs
            + ("".join(item.replay_thought_chunks),),
        )
    if probe_id == "message_grouping":
        item = cast(MessageGroupingEvidence, evidence)
        return replace(item, missing_id_replay_group_ids=("drifted-group",))
    if probe_id == "plan_tool_reconciliation":
        item = cast(PlanToolEvidence, evidence)
        return replace(item, final_plan=("First snapshot",), tool_call_ids=("wrong-tool",))
    if probe_id == "permission_exactly_once":
        item = cast(PermissionSettlementEvidence, evidence)
        return replace(item, settlements_by_cause=(("cancel", 2),) + item.settlements_by_cause[1:])
    if probe_id == "durable_refresh":
        item = cast(RefreshEvidence, evidence)
        return replace(
            item,
            refreshed_session_id="drifted-session",
            refreshed_binding_generation=99,
        )
    if probe_id == "delivery_capabilities":
        item = cast(DeliveryCapabilityEvidence, evidence)
        return replace(
            item,
            steer_result="inferred_from_backend_name",
            broker_operations=("queue",),
        )
    if probe_id == "compaction_visibility":
        item = cast(CompactionEvidence, evidence)
        return replace(item, automatic_states=())
    if probe_id == "callback_wire_order":
        item = cast(CallbackOrderEvidence, evidence)
        return replace(item, broadcast_order=tuple(reversed(item.wire_order)))
    if probe_id == "protocol_rejection":
        item = cast(ProtocolRejectionEvidence, evidence)
        return replace(item, assistant_text_fallbacks=("must never become assistant text",))
    raise KeyError(probe_id)
