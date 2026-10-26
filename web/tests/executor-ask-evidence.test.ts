import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import PermissionAskActions from "../src/components/conversation/PermissionAskActions.svelte";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import { executorAnswerAvailable, currentExecutorAskRecord, currentExecutorAnswers, executorAnswerSentence, executorUnavailableSentence } from "../src/lib/conversation/executorEvidence";
import type { ConversationView, ExecutorAskEvidence, ExecutorAskRecord } from "../src/lib/conversation/wire";

const child = { executor_incarnation: "mac-incarnation", child_id: "child", conversation_id: "conversation" };
const turn = { conversation_id: "conversation", turn_number: 3 };
const evidence = (kind: "permission" | "user_input", ask_id: string): ExecutorAskEvidence => ({
  ask: { child, turn, kind, ask_id }, connection_generation: 4, reachability: "reachable",
  observed_at_ms: 99, answer_id: null, applied_at_mac_ms: null
});
function snapshot(): ConversationView {
  return {
    conversation_id: "conversation", backend_key: "codex", supports_steer: false, model: null,
    reasoning_effort: null, workspace_folder: "/workspace", access: "full",
 latest_sequence: 10, owner_read_through_sequence: 0,
    is_running: true, held_prompts: [], executor_context: { child, turn },
    pending_permission_ask: { ask_id: "permission", title: "Allow?", detail: null, options: [], answer_available: true, executor_evidence: evidence("permission", "permission") },
    pending_user_input: { request_id: "questions", questions: [], answer_available: false, executor_evidence: evidence("user_input", "questions") }
  };
}

describe("exact executor ask evidence", () => {
  it("enables only the individually attested request, including multi-question input", () => {
    const view = snapshot();
    expect(executorAnswerAvailable(view, "permission", "permission", "macos")).toBe(true);
    expect(executorAnswerAvailable(view, "user_input", "questions", "macos")).toBe(false);
    view.pending_user_input!.answer_available = true;
    expect(executorAnswerAvailable(view, "user_input", "questions", "macos")).toBe(true);
    view.pending_user_input!.executor_evidence!.ask.kind = "permission";
    expect(executorAnswerAvailable(view, "user_input", "questions", "macos")).toBe(false);
  });

  it("accepts disconnected and restarted snapshots but rejects stale or missing identity", () => {
    const view = snapshot();
    expect(executorAnswerAvailable(view, "permission", "permission", "macos")).toBe(true);
    expect(executorAnswerAvailable(view, "permission", "permission", "macos", false)).toBe(false);
    expect(executorAnswerAvailable(view, "permission", "old-transcript-request", "macos")).toBe(false);
    view.pending_permission_ask!.executor_evidence = null;
    expect(executorAnswerAvailable(view, "permission", "permission", "macos")).toBe(false);
  });

  it.each(["incarnation", "child", "turn", "conversation", "answer", "child_gone", "callback_gone"])("rejects stale or spent %s evidence", (mismatch) => {
    const view = snapshot();
    const proof = view.pending_permission_ask!.executor_evidence!;
    if (mismatch === "incarnation") proof.ask.child = { ...child, executor_incarnation: "old" };
    if (mismatch === "child") proof.ask.child = { ...child, child_id: "other" };
    if (mismatch === "turn") proof.ask.turn = { ...turn, turn_number: 2 };
    if (mismatch === "conversation") proof.ask.turn = { ...turn, conversation_id: "other" };
    if (mismatch === "answer") proof.answer_id = "recorded";
    if (mismatch === "child_gone" || mismatch === "callback_gone") proof.reachability = mismatch;
    expect(executorAnswerAvailable(view, "permission", "permission", "macos")).toBe(false);
  });

  it("accepts uncertain callbacks with persisted identity regardless of generation", () => {
    const view = snapshot();
    const proof = view.pending_permission_ask!.executor_evidence!;
    proof.reachability = "uncertain";
    proof.connection_generation = 1;
    expect(executorAnswerAvailable(view, "permission", "permission", "macos")).toBe(true);
    view.pending_permission_ask!.answer_available = false;
    expect(executorAnswerAvailable(view, "permission", "permission", "macos")).toBe(false);
  });

  it("keeps Mac controls closed when remote ownership/status is absent and preserves local behavior", () => {
    const view = snapshot();
    view.executor_context = null;
    delete view.pending_permission_ask!.answer_available;
    delete view.pending_permission_ask!.executor_evidence;
    expect(executorAnswerAvailable(view, "permission", "permission", "macos")).toBe(false);
    expect(executorAnswerAvailable(view, "permission", "permission", "vps")).toBe(true);
  });

  it("uses the public request and its attested native binding independently", () => {
    const view = snapshot();
    view.pending_permission_ask!.ask_id = "turn-public:permission:native";
    view.pending_permission_ask!.executor_evidence!.ask.ask_id = "native";
    expect(executorAnswerAvailable(view,"permission","turn-public:permission:native","macos")).toBe(true);
    expect(executorAnswerAvailable(view,"permission","native","macos")).toBe(false);
    const proof=view.pending_permission_ask!.executor_evidence!;
    view.executor_asks=[{evidence:proof,answer:null}];
    expect(currentExecutorAskRecord(view,"permission","turn-public:permission:native")).toBe(view.executor_asks[0]);
  });
  it("retains current receipts after pending clears without attributing them to reused transcript IDs", () => {
    const view=snapshot();
    const proof=view.pending_permission_ask!.executor_evidence!;
    view.executor_asks=[{evidence:proof,answer:{answer_id:"accepted",ask:proof.ask,accepted_at_vps_ms:100}}];
    view.pending_permission_ask=null;
    expect(currentExecutorAnswers(view)).toEqual(view.executor_asks);
    view.executor_context={child:{...child,child_id:"replacement"},turn};
    expect(currentExecutorAnswers(view)).toEqual([]);
  });
  it("retains durable acceptance independently from callback application", () => {
    const proof = evidence("permission", "permission");
    const record: ExecutorAskRecord = { evidence: proof, answer: { answer_id: "answer", ask: proof.ask, accepted_at_vps_ms: 100 } };
    expect(executorAnswerSentence(record)).toBe("Answer saved on VPS; delivery to Mac is pending.");
    proof.reachability = "callback_gone";
    expect(executorAnswerSentence(record)).toBe("Answer saved on VPS; the original Mac request is gone. Application is unconfirmed.");
    proof.answer_id = "answer";
    proof.applied_at_mac_ms = 101;
    proof.reachability = "child_gone";
    expect(executorAnswerSentence(record)).toBe("Answer applied on Mac.");
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
    expect(open).toContain("Current activity on Mac is unknown.");
    expect(open).not.toContain("live-text-shimmer");
    const complete = render(TurnAnchor, { props: { activityUncertain: true, settled: true, ending: "completed", durationSeconds: 3 } }).body;
    expect(complete).not.toContain("Activity unknown");
  });
});
