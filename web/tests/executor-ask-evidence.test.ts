import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import PermissionAskActions from "../src/components/conversation/PermissionAskActions.svelte";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import { executorAnswerAvailable, executorAnswerSentence } from "../src/lib/conversation/executorEvidence";
import type { ConversationView } from "../src/lib/conversation/wire";

function snapshot(): ConversationView {
  return {conversation_id:"c", backend_key:"codex", supports_steer:false, model:null,
    reasoning_effort:null,workspace_folder:"/workspace",access:"full",latest_sequence:10,
    owner_read_through_sequence:0,is_running:true,held_prompts:[],
    pending_permission_ask:{ask_id:"current-permission",title:"Allow?",detail:null,options:[]},
    pending_user_input:{request_id:"current-input",questions:[]}};
}

describe("conversation answer fate", () => {
  it("allows the current request during an outage but refuses stale and closed snapshots", () => {
    const view=snapshot();
    expect(executorAnswerAvailable(view,"permission","current-permission","macos")).toBe(true);
    expect(executorAnswerAvailable(view,"user_input","current-input","macos")).toBe(true);
    expect(executorAnswerAvailable(view,"permission","old-permission","macos")).toBe(false);
    expect(executorAnswerAvailable(view,"permission","current-permission","macos",false)).toBe(false);
    view.is_running=false;
    expect(executorAnswerAvailable(view,"permission","current-permission","macos")).toBe(false);
    view.is_running=true;view.pending_permission_ask=null;
    expect(executorAnswerAvailable(view,"permission","current-permission","macos")).toBe(false);
  });
  it("keeps saved, proven application, and uncertain closed delivery distinct", () => {
    expect(executorAnswerSentence("saved")).toBe("Answer saved on VPS; delivery to Mac is pending.");
    expect(executorAnswerSentence("applied")).toBe("Answer applied on Mac.");
    expect(executorAnswerSentence("unconfirmed")).toContain("Application is unconfirmed.");
    expect(executorAnswerSentence(undefined)).toBeNull();
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
