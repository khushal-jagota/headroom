from __future__ import annotations

import asyncio
import json

import pytest
from acp.connection import StreamDirection
from acp.schema import AgentThoughtChunk
from tests.support.acp_conformance import (
    MutatedAcpConformanceSubject,
    assert_all_probes,
    assert_probe,
    load_conformance_manifest,
)
from tests.support.acp_reference_subject import (
    ReferenceAcpConformanceSubject,
    run_deterministic_death_exercise,
)


@pytest.fixture(scope="module")
def reference_subject() -> ReferenceAcpConformanceSubject:
    return asyncio.run(ReferenceAcpConformanceSubject.create())


def test_scripted_agent_uses_official_protocol_lifecycle_and_stderr_diagnostics(
    reference_subject: ReferenceAcpConformanceSubject,
) -> None:
    exercise = reference_subject.process_exercise
    assert (exercise.agent_name, exercise.agent_version) == ("panels-scripted-agent", "1.0.0")
    assert exercise.session_id == "scripted-session-1"
    assert exercise.default_prompt_response.stop_reason == "end_turn"
    assert exercise.delayed_prompt_response.stop_reason == "end_turn"
    assert exercise.cancel_prompt_response.stop_reason == "cancelled"
    assert exercise.process_return_code == 0

    outgoing_methods = {
        event.message.get("method")
        for event in exercise.stream_events
        if event.direction is StreamDirection.OUTGOING
    }
    assert {
        "initialize",
        "session/new",
        "session/load",
        "session/prompt",
        "session/cancel",
    } <= outgoing_methods
    assert exercise.load_replay_positions
    assert max(exercise.load_replay_positions) < exercise.load_response_position
    assert any(isinstance(item.update, AgentThoughtChunk) for item in exercise.replay_default)

    assert len(exercise.permission_requests) == 1
    permission_request = exercise.permission_requests[0]
    assert permission_request.session_id == exercise.session_id
    assert [option.option_id for option in permission_request.options] == [
        "allow-once",
        "reject-once",
    ]

    incoming_events = [
        event
        for event in exercise.stream_events
        if event.direction is StreamDirection.INCOMING
    ]
    assert incoming_events
    assert all(event.message.get("jsonrpc") == "2.0" for event in incoming_events)
    assert "scripted-acp-agent:" not in json.dumps(
        [event.message for event in incoming_events]
    )
    assert "scripted-acp-agent: initialize" in exercise.stderr_text
    assert "scripted-acp-agent: load scripted-session-1" in exercise.stderr_text
    assert "scripted-acp-agent: cancel scripted-session-1" in exercise.stderr_text


def test_scripted_agent_dies_deterministically_with_nonzero_status() -> None:
    death = asyncio.run(run_deterministic_death_exercise())
    assert death.process_return_code == 23
    assert death.prompt_failed
    assert "deterministic death" in death.stderr_text


def test_manifest_has_exactly_ten_unique_ordered_probes_and_production_owners() -> None:
    manifest = load_conformance_manifest()
    assert [probe.probe_id for probe in manifest] == [
        "load_replay_before_response",
        "typed_thought",
        "message_grouping",
        "plan_tool_reconciliation",
        "permission_exactly_once",
        "durable_refresh",
        "delivery_capabilities",
        "compaction_visibility",
        "callback_wire_order",
        "protocol_rejection",
    ]
    assert len({probe.probe_id for probe in manifest}) == 10
    assert [probe.production_owner for probe in manifest] == [
        "ACP-01",
        "ACP-03",
        "ACP-03",
        "ACP-03",
        "ACP-02",
        "ACP-01",
        "ACP-02",
        "ACP-02",
        "ACP-01",
        "ACP-03",
    ]
    assert [probe.description for probe in manifest] == [
        "load replay finishes before the load response",
        "live/replayed thought stays typed and never assistant text",
        "stable message IDs and deterministic missing-ID turn fallback group correctly",
        "plan snapshots replace and tool progress reconciles by tool-call ID",
        "permission cancel/death/disconnect/timeout settles exactly once",
        "refresh targets the same durable employee session/binding generation",
        "steer is real or visibly unavailable; common Queue and Send Now remain broker-owned",
        "explicit/automatic compaction has live state and inspectable completion",
        "concurrent SDK callbacks reduce/broadcast in wire order",
        "unknown/partial replay yields protocol_update_rejected, never text fallback",
    ]


def test_reference_subject_passes_every_reusable_probe(
    reference_subject: ReferenceAcpConformanceSubject,
) -> None:
    assert_all_probes(reference_subject)


@pytest.mark.parametrize(
    "mutated_probe_id",
    [
        "load_replay_before_response",
        "typed_thought",
        "message_grouping",
        "plan_tool_reconciliation",
        "permission_exactly_once",
        "durable_refresh",
        "delivery_capabilities",
        "compaction_visibility",
        "callback_wire_order",
        "protocol_rejection",
    ],
)
def test_each_probe_mutation_fails_only_its_matching_assertion(
    reference_subject: ReferenceAcpConformanceSubject,
    mutated_probe_id: str,
) -> None:
    mutated_subject = MutatedAcpConformanceSubject(reference_subject, mutated_probe_id)
    failed_probe_ids: list[str] = []
    for probe in load_conformance_manifest():
        try:
            assert_probe(mutated_subject, probe.probe_id)
        except AssertionError:
            failed_probe_ids.append(probe.probe_id)
    assert failed_probe_ids == [mutated_probe_id]
