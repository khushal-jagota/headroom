import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import PermissionAskActions from "../src/components/conversation/PermissionAskActions.svelte";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import { executorAnswerAvailable, executorAnswerSentence, executorUnavailableSentence, historicalExecutorAskRecord } from "../src/lib/conversation/executorEvidence";
import type { ConversationView, ExecutorAskEvidence, ExecutorAskRecord } from "../src/lib/conversation/wire";
import type { ExecutorHealth } from "../src/lib/ticketMachines";

const child = { executor_incarnation: "mac-incarnation", child_id: "child", conversation_id: "conversation" };
const turn = { conversation_id: "conversation", turn_number: 3 };
const health: ExecutorHealth = {
  mode: "remote", identity: { executor_incarnation: "mac-incarnation", connection_generation: 4 },
  connection_state: "ready", reconciliation_ready: true,
  last_contact_at_ms: 100, last_contact_source: "vps_received_valid_executor_frame"
};
const evidence = (kind: "permission" | "user_input", ask_id: string): ExecutorAskEvidence => ({
  ask: { child, turn, kind, ask_id }, connection_generation: 4, reachability: "reachable",
  observed_at_ms: 99, answer_id: null, applied_at_mac_ms: null
});
function snapshot(): ConversationView {
  return {
    conversation_id: "conversation", backend_key: "codex", supports_steer: false, model: null,
    reasoning_effort: null, workspace_folder: "/workspace", access: "full", role_text: null,
    identity_environment_variable_names: [], latest_sequence: 10, owner_read_through_sequence: 0,
    is_running: true, held_prompts: [], composer_catalog: [], executor_context: { child, turn },
    pending_permission_ask: { ask_id: "permission", title: "Allow?", detail: null, options: [], answer_available: true, executor_evidence: evidence("permission", "permission") },
    pending_user_input: { request_id: "questions", questions: [], answer_available: false, executor_evidence: evidence("user_input", "questions") }
  };
}

describe("exact executor ask evidence", () => {
  it("enables only the individually attested request, including multi-question input", () => {
    const view = snapshot();
    expect(executorAnswerAvailable(view, "permission", "permission", health, "macos")).toBe(true);
    expect(executorAnswerAvailable(view, "user_input", "questions", health, "macos")).toBe(false);
    view.pending_user_input!.answer_available = true;
    expect(executorAnswerAvailable(view, "user_input", "questions", health, "macos")).toBe(true);
    view.pending_user_input!.executor_evidence!.ask.kind = "permission";
    expect(executorAnswerAvailable(view, "user_input", "questions", health, "macos")).toBe(false);
  });

  it("does not turn reconnect, receipt contact, or stale snapshots into evidence", () => {
    const view = snapshot();
    for (const state of [undefined, { ...health, connection_state: "reconciling" as const }, { ...health, connection_state: "disconnected" as const }, { ...health, reconciliation_ready: false }]) {
      expect(executorAnswerAvailable(view, "permission", "permission", state, "macos")).toBe(false);
    }
    expect(executorAnswerAvailable(view, "permission", "permission", health, "macos", false)).toBe(false);
    expect(executorAnswerAvailable(view, "permission", "old-transcript-request", health, "macos")).toBe(false);
    view.pending_permission_ask!.executor_evidence = null;
    expect(executorAnswerAvailable(view, "permission", "permission", { ...health, last_contact_at_ms: 9999 }, "macos")).toBe(false);
  });

  it.each(["generation", "incarnation", "child", "turn", "conversation", "answer", "reachability"])("rejects stale or spent %s evidence", (mismatch) => {
    const view = snapshot();
    const proof = view.pending_permission_ask!.executor_evidence!;
    if (mismatch === "generation") proof.connection_generation -= 1;
    if (mismatch === "incarnation") proof.ask.child = { ...child, executor_incarnation: "old" };
    if (mismatch === "child") proof.ask.child = { ...child, child_id: "other" };
    if (mismatch === "turn") proof.ask.turn = { ...turn, turn_number: 2 };
    if (mismatch === "conversation") proof.ask.turn = { ...turn, conversation_id: "other" };
    if (mismatch === "answer") proof.answer_id = "recorded";
    if (mismatch === "reachability") proof.reachability = "uncertain";
    expect(executorAnswerAvailable(view, "permission", "permission", health, "macos")).toBe(false);
  });

  it("keeps Mac controls closed when remote ownership/status is absent and preserves local behavior", () => {
    const view = snapshot();
    view.executor_context = null;
    delete view.pending_permission_ask!.answer_available;
    delete view.pending_permission_ask!.executor_evidence;
    expect(executorAnswerAvailable(view, "permission", "permission", health, "macos")).toBe(false);
    expect(executorAnswerAvailable(view, "permission", "permission", undefined, "vps")).toBe(true);
  });

  it("retains durable acceptance independently from callback application", () => {
    const proof = evidence("permission", "permission");
    const record: ExecutorAskRecord = { evidence: proof, answer: { answer_id: "answer", ask: proof.ask, accepted_at_vps_ms: 100 } };
    expect(executorAnswerSentence(record)).toBe("Answer recorded on VPS; application on Mac is unconfirmed.");
    proof.answer_id = "answer";
    proof.applied_at_mac_ms = 101;
    proof.reachability = "child_gone";
    expect(executorAnswerSentence(record)).toBe("Answer applied on Mac.");
  });

  it("does not attach a recorded answer to a reused ID in another turn or child", () => {
    const first: ExecutorAskRecord = { evidence: evidence("permission", "reused"), answer: null };
    const second = structuredClone(first);
    second.evidence.ask.turn.turn_number += 1;
    second.evidence.ask.child.child_id = "other-child";
    second.answer = { answer_id: "other-answer", ask: second.evidence.ask, accepted_at_vps_ms: 100 };
    expect(historicalExecutorAskRecord([first, second], "permission", "reused", 1)).toBeUndefined();
    expect(historicalExecutorAskRecord([second], "permission", "reused", 2)).toBeUndefined();
    expect(historicalExecutorAskRecord([second], "permission", "reused", 1)).toBe(second);
  });

  it.each(["child_gone", "callback_gone"] as const)("names confirmed %s instead of claiming an unknown pending callback", (reachability) => {
    expect(executorUnavailableSentence({ ...evidence("permission", "permission"), reachability })).toContain("cannot accept an answer");
    expect(executorUnavailableSentence({ ...evidence("permission", "permission"), reachability })).not.toContain("still pending");
  });
});

describe("unavailable request controls", () => {
  const noAction = () => {};
  it("disables permission answers and numbered question choices while preserving Stop", () => {
    const approval = { askId: "permission", title: "Allow?", detail: null, options: [{ option_id: "yes", label: "Allow", option_kind: "allow_once" }] };
    const actions = render(PermissionAskActions, { props: { ask: approval, answerAvailable: false, onAnswer: noAction, onCancelTurn: noAction } }).body;
    expect(actions).toMatch(/data-conversation-ask-action="yes"[^>]*disabled/);
    expect(actions).not.toMatch(/data-conversation-ask-action="cancel_turn"[^>]*disabled/);
    const question = render(PermissionAskCard, { props: { ask: { ...approval, options: [{ option_id: "one", label: "One", option_kind: "choice" }] }, answerAvailable: false, onAnswer: noAction } }).body;
    expect(question).toContain("disabled");
  });
  it("disables multi-question choices, text and submit without hiding the request", () => {
    const html = render(UserInputQuestionPanel, { props: { request: { requestId: "questions", questions: [{ question_id: "first", header: "First", question: "Which?", options: [{ label: "One", description: "" }], multi_select: false, allow_other: true }, { question_id: "second", header: "Second", question: "Then?", options: [{ label: "Two", description: "" }], multi_select: true, allow_other: false }] }, answerAvailable: false } }).body;
    expect(html).toMatch(/data-user-input-option="One"[^>]*disabled/);
    expect(html).toMatch(/data-user-input-other[^>]*disabled/);
    expect(html).toMatch(/data-user-input-continue[^>]*disabled/);
    expect(html).not.toMatch(/data-user-input-cancel[^>]*disabled/);
  });
  it("shows uncertainty without inventing activity or overwriting completed turns", () => {
    const open = render(TurnAnchor, { props: { activityUncertain: true } }).body;
    expect(open).toContain("Activity unknown. Last progress remains below.");
    expect(open).not.toContain("live-text-shimmer");
    const complete = render(TurnAnchor, { props: { activityUncertain: true, settled: true, ending: "completed", durationSeconds: 3 } }).body;
    expect(complete).not.toContain("Activity unknown");
  });
});
