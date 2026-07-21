"""Adapt browser-produced JSON evidence to the frozen ACP-00 conformance probes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from acp_conformance import (
    MessageGroupingEvidence,
    PlanToolEvidence,
    ProtocolRejectionEvidence,
    ThoughtEvidence,
    assert_message_grouping,
    assert_plan_tool_reconciliation,
    assert_protocol_rejection,
    assert_typed_thought,
    mutate_probe_evidence,
)


def _tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AssertionError("browser conformance evidence must contain string arrays")
    return tuple(value)


def _thought(raw: dict[str, object]) -> ThoughtEvidence:
    return ThoughtEvidence(
        live_update_types=_tuple(raw["liveUpdateTypes"]),
        replay_update_types=_tuple(raw["replayUpdateTypes"]),
        live_thought_chunks=_tuple(raw["liveThoughtChunks"]),
        replay_thought_chunks=_tuple(raw["replayThoughtChunks"]),
        live_assistant_outputs=_tuple(raw["liveAssistantOutputs"]),
        replay_assistant_outputs=_tuple(raw["replayAssistantOutputs"]),
    )


def _grouping(raw: dict[str, object]) -> MessageGroupingEvidence:
    return MessageGroupingEvidence(
        stable_group_ids=_tuple(raw["stableGroupIds"]),
        missing_id_live_group_ids=_tuple(raw["missingIdLiveGroupIds"]),
        missing_id_replay_group_ids=_tuple(raw["missingIdReplayGroupIds"]),
        boundary_reset_group_ids=_tuple(raw["boundaryResetGroupIds"]),
    )


def _plan_tool(raw: dict[str, object]) -> PlanToolEvidence:
    observed = raw["observedPlanSnapshots"]
    if not isinstance(observed, list):
        raise AssertionError("observed plan snapshots must be an array")
    return PlanToolEvidence(
        observed_plan_snapshots=tuple(_tuple(item) for item in observed),
        final_plan=_tuple(raw["finalPlan"]),
        tool_call_ids=_tuple(raw["toolCallIds"]),
        final_tool_status=str(raw["finalToolStatus"]),
    )


def _protocol(raw: dict[str, object]) -> ProtocolRejectionEvidence:
    return ProtocolRejectionEvidence(
        rejected_discriminators=_tuple(raw["rejectedDiscriminators"]),
        visible_statuses=_tuple(raw["visibleStatuses"]),
        assistant_text_fallbacks=_tuple(raw["assistantTextFallbacks"]),
    )


def assert_browser_conformance(raw: dict[str, object]) -> None:
    evidence = {
        "typed_thought": _thought(raw["typedThought"]),  # type: ignore[arg-type]
        "message_grouping": _grouping(raw["messageGrouping"]),  # type: ignore[arg-type]
        "plan_tool_reconciliation": _plan_tool(raw["planToolReconciliation"]),  # type: ignore[arg-type]
        "protocol_rejection": _protocol(raw["protocolRejection"]),  # type: ignore[arg-type]
    }
    assertions = {
        "typed_thought": assert_typed_thought,
        "message_grouping": assert_message_grouping,
        "plan_tool_reconciliation": assert_plan_tool_reconciliation,
        "protocol_rejection": assert_protocol_rejection,
    }
    for probe_id, probe_evidence in evidence.items():
        assertion = assertions[probe_id]
        assertion(probe_evidence)  # type: ignore[arg-type]
        mutated = mutate_probe_evidence(probe_id, probe_evidence)
        try:
            assertion(mutated)  # type: ignore[arg-type]
        except AssertionError:
            continue
        raise AssertionError(f"ACP-00 mutation did not fail browser probe {probe_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    arguments = parser.parse_args()
    raw = json.loads(arguments.evidence.read_text())
    if not isinstance(raw, dict):
        raise AssertionError("browser evidence root must be an object")
    assert_browser_conformance(raw)
    print("acp_browser_conformance.py: probes 2, 3, 4, and 10 passed; mutations failed")


if __name__ == "__main__":
    main()
