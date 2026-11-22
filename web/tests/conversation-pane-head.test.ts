import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import { awaitsCredential, conversationState, copyText, type ConversationStateFacts, interruptedFrom, paneFailureFrom, paneHeadStatus, paneTitleFor, uncertainActivityFeedback } from "../src/lib/conversation/dockPresentation";
import type { ConversationViewItem, CredentialRequestRowBody } from "../src/lib/conversation/view/contracts";
import type { TranscriptRow } from "../src/lib/conversation/transcript";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";

const quiet: ConversationStateFacts = { readOnly: false, activityUncertain: false, waitingForYou: false, running: false, backgroundWorking: false, settingUp: null, elapsed: null };

describe("the opened pane's top bar", () => {
  it("names who the conversation is with, from the page or else the role", () => {
    expect(paneTitleFor("Vylo pricing and launch", "Manager")).toBe("Vylo pricing and launch");
    expect(paneTitleFor("  ", "Chief")).toBe("Chief of Staff");
    expect(paneTitleFor(null, "Worker")).toBe("Worker");
  });

  it("says each state once: the top bar only says what the thread has no place for", () => {
    expect(paneHeadStatus(quiet)).toBeNull();
    // Working is the running turn's own line; waiting is the docked ask's; neither repeats here.
    expect(paneHeadStatus({ ...quiet, running: true, elapsed: "2m 14s" })).toBeNull();
    expect(paneHeadStatus({ ...quiet, running: true, waitingForYou: true })).toBeNull();
    expect(paneHeadStatus({ ...quiet, backgroundWorking: true })).toBeNull();
    expect(conversationState({ ...quiet, running: true, elapsed: "2m 14s" })).toMatchObject({ state: "working", text: "Working · 2m 14s", mark: "spinner" });
    expect(conversationState({ ...quiet, running: true, waitingForYou: true, waitingFor: "approval" })).toMatchObject({ state: "waiting", text: "Needs your approval", mark: "dot" });
    expect(conversationState({ ...quiet, waitingForYou: true, waitingFor: "answer" }).text).toBe("Needs your answer");
    expect(conversationState({ ...quiet, waitingForYou: true, waitingFor: "key" }).text).toBe("Needs a key");
    expect(paneHeadStatus({ ...quiet, settingUp: "Setting up · preparing workspace · 1m 35s" }))
      .toMatchObject({ state: "setting-up", text: "Setting up · preparing workspace · 1m 35s", mark: "spinner" });
    expect(paneHeadStatus({ ...quiet, running: true, activityUncertain: true })?.state).toBe("unknown");
    expect(paneHeadStatus({ ...quiet, running: true, readOnly: true })?.text).toBe("Past conversation · read only");
  });

  it("says a failure honestly, from what last happened", () => {
    expect(paneFailureFrom({ lastBoundaryFailed: true, latestKind: "turn_ended" })).toBe("turn_failed");
    // A refused message is the newest thing; the failed turn before it is not repeated.
    expect(paneFailureFrom({ lastBoundaryFailed: false, latestKind: "prompt_refused" })).toBe("not_delivered");
    expect(paneFailureFrom({ lastBoundaryFailed: true, latestKind: "prompt_refused" })).toBe("not_delivered");
    // A message sent after a failure is the person moving on.
    expect(paneFailureFrom({ lastBoundaryFailed: false, latestKind: "prompt" })).toBeNull();
    expect(paneFailureFrom({ lastBoundaryFailed: false, latestKind: "prompt_uncertain" })).toBeNull();
    // The failure's callout in the thread says it; the top bar does not say it again.
    expect(paneHeadStatus({ ...quiet, failure: "turn_failed" })).toBeNull();
    expect(conversationState({ ...quiet, failure: "turn_failed" })).toMatchObject({ state: "failed", text: "Turn failed", mark: "alert" });
    expect(conversationState({ ...quiet, failure: "not_delivered" })).toMatchObject({ state: "undelivered", text: "Message not delivered", mark: "alert" });
    expect(paneHeadStatus({ ...quiet, failure: "turn_failed", readOnly: true })?.state).toBe("read-only");
  });

  it("reads one state in one order: waiting, failed, working, setting up, interrupted, idle", () => {
    const setup = "Setting up · preparing workspace";
    const all = { ...quiet, waitingForYou: true, failure: "turn_failed" as const, running: true, settingUp: setup, interrupted: true };
    expect(conversationState(all).state).toBe("waiting");
    expect(conversationState({ ...all, waitingForYou: false }).state).toBe("failed");
    expect(conversationState({ ...all, waitingForYou: false, failure: "not_delivered" }).state).toBe("undelivered");
    expect(conversationState({ ...all, waitingForYou: false, failure: null }).state).toBe("working");
    expect(conversationState({ ...all, waitingForYou: false, failure: null, running: false }).state).toBe("setting-up");
    expect(conversationState({ ...all, waitingForYou: false, failure: null, running: false, settingUp: null })).toMatchObject({ state: "interrupted", text: "Interrupted", mark: "stop" });
    expect(conversationState(quiet)).toMatchObject({ state: "idle", text: "", mark: null });
  });

  it("never names a tool or a command, so it reads the same in Focus and Full", () => {
    for (const facts of [{ ...quiet, running: true, elapsed: "41s" }, { ...quiet, backgroundWorking: true }, { ...quiet, waitingForYou: true }]) {
      expect(conversationState(facts).text).not.toMatch(/Running|Reading|Editing|go test|\$/);
    }
  });

  it("knows a stopped turn from what last happened", () => {
    expect(interruptedFrom({ kind: "turn_stopped" })).toBe(true);
    expect(interruptedFrom({ kind: "turn_ended", ending: "interrupted" })).toBe(true);
    expect(interruptedFrom({ kind: "turn_ended", ending: "completed" })).toBe(false);
    expect(interruptedFrom({ kind: "prompt" })).toBe(false);
    expect(interruptedFrom(null)).toBe(false);
  });
});

describe("the dock's status line", () => {
  const rest = (facts: Partial<ConversationStateFacts>, props: Record<string, unknown> = {}) =>
    render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState({ ...quiet, ...facts }), ...props } }).body;
  it("reads the same derived state the header does", () => {
    expect(rest({})).toContain("Worker idle");
    expect(rest({ running: true, elapsed: "2m 14s" })).toContain("Working · 2m 14s");
    const waiting = rest({ waitingForYou: true, waitingFor: "approval" }, { waitingTitle: "Upload build 41 to TestFlight" });
    expect(waiting).toContain("Needs your approval");
    expect(waiting).toContain(" — Upload build 41 to TestFlight");
    expect(waiting).toContain('data-conversation-rest-state="waiting"');
    expect(rest({ failure: "turn_failed" }, { feedback: "rate limit · resets in 41m" })).toContain("Turn failed");
    expect(rest({ failure: "not_delivered" }, { feedback: "not delivered · the machine is offline" })).toMatch(/ — the machine is offline/);
  });
  it("opens the conversation from the whole line, with its words on a wide screen", () => {
    const html = rest({});
    expect(html).toMatch(/<button[^>]*data-conversation-rest-bar/);
    expect(html).toContain("Open conversation");
  });
});

describe("setting up while the machine cannot be read", () => {
  const workerSetup = "Setting up · preparing workspace";
  it("stays setup, with one caution chip and no second sentence", () => {
    expect(uncertainActivityFeedback(true)).toBe("This machine is not connected, so setup progress cannot be confirmed.");
    expect(uncertainActivityFeedback(false)).toBe("Current activity on this machine is unknown.");
    const state = conversationState({ ...quiet, activityUncertain: true, settingUp: workerSetup });
    expect(state.state).toBe("setting-up");
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state, offline: true,
      feedback: uncertainActivityFeedback(true) } }).body;
    expect(html).toContain("Setting up · preparing workspace");
    expect(html).toContain("data-unconfirmed");
    expect(html).toContain("Machine offline");
    // The reason is the chip's tooltip and a screen reader's status, never a drawn second line.
    expect(html).not.toContain("c2-rest-feedback");
  });
  it("says the activity is unknown once when nothing is being set up", () => {
    const state = conversationState({ ...quiet, activityUncertain: true });
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state, offline: true,
      feedback: uncertainActivityFeedback(false) } }).body;
    expect(html.match(/Current activity on this machine is unknown\./g)).toHaveLength(1);
  });
  it("keeps the moving mark while setup is known", () => {
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState({ ...quiet, settingUp: workerSetup }) } }).body;
    expect(html).toContain('aria-label="setting up"');
    expect(html).not.toContain("data-unconfirmed");
  });
});

describe("a key request waiting on the person", () => {
  const request: CredentialRequestRowBody = {
    kind: "credential_request", request_id: "r-1", name: "SENTRY_AUTH_TOKEN", provider: "Sentry",
    use: "Upload dSYMs.", auth_kind: "bearer", header_name: null, help: null, help_url: null,
    replacement: null, state: "pending", credential: null, delivery: null
  };
  const item = (state: CredentialRequestRowBody["state"], changed: number): ConversationViewItem => ({
    kind: "row", key: "row-1", row_kind: "credential_request", order: { sequence: 4, slot: 0 },
    source_sequence: 4, sender_message_id: null, changed_through_sequence: changed, created_at: 0,
    turn_key: null, work_group_key: null, fold_rule: "never",
    body: { kind: "inline", value: { ...request, state } }
  });
  it("counts as waiting for you while it is pending, from the record's rows or the view's", () => {
    const row = { key: "e4", kind: "credential_request", sequence: 4, createdAt: 0, request } as TranscriptRow;
    expect(awaitsCredential([row])).toBe(true);
    expect(awaitsCredential([], [item("pending", 4)])).toBe(true);
    expect(awaitsCredential([], [])).toBe(false);
  });
  it("lets the newest version of the row speak for it", () => {
    expect(awaitsCredential([], [item("pending", 4), item("saved", 6)])).toBe(false);
    expect(awaitsCredential([], [item("saved", 6), item("pending", 4)])).toBe(false);
  });
});

describe("the options menu's workspace fact", () => {
  it("copies the path, and says Copied only when the clipboard took it", async () => {
    const written: string[] = [];
    expect(await copyText("/Users/k/panels", { writeText: async (text: string) => { written.push(text); } })).toBe(true);
    expect(written).toEqual(["/Users/k/panels"]);
    expect(await copyText("/x", { writeText: async () => { throw new Error("denied"); } })).toBe(false);
    expect(await copyText("/x", undefined)).toBe(false);
  });
});
