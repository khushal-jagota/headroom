import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import { awaitsCredential, conversationState, copyText, dockStatusFrom, type ConversationStateFacts, type DockStatus, interruptedFrom, paneFailureFrom, paneHeadStatus, paneTitleFor } from "../src/lib/conversation/dockPresentation";
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
  const clocks = { offline: false, setupClock: "1m 35s", turnClock: "2m 14s" };
  const status = (facts: Partial<ConversationStateFacts>, offline = false) =>
    dockStatusFrom({ ...clocks, offline, state: conversationState({ ...quiet, ...facts }).state });
  it("says one of four states, whatever else waits", () => {
    expect(status({ settingUp: "Setting up · preparing workspace · 1m 35s" })).toEqual({ kind: "setting-up", clock: "1m 35s" });
    expect(status({ running: true })).toEqual({ kind: "working", clock: "2m 14s" });
    expect(status({})).toEqual({ kind: "idle", clock: null });
    // An ask, a proposal, a failure or a stop says itself elsewhere; the dock says Idle.
    for (const facts of [{ running: true, waitingForYou: true, waitingFor: "answer" as const }, { waitingForYou: true, waitingFor: "approval" as const },
      { failure: "turn_failed" as const }, { failure: "not_delivered" as const }, { interrupted: true }]) {
      expect(status(facts)).toEqual({ kind: "idle", clock: null });
    }
  });
  it("says only Machine offline while the machine cannot be read, setting up or not", () => {
    expect(status({ activityUncertain: true, settingUp: "Setting up" }, true)).toEqual({ kind: "offline", clock: null });
    expect(status({ activityUncertain: true, running: true }, true)).toEqual({ kind: "offline", clock: null });
  });
  const rest = (status: DockStatus, props: Record<string, unknown> = {}) =>
    render(ConversationRestBar, { props: { onOpen: () => {}, status, ...props } }).body.replace(/<!--[^]*?-->/g, "");
  it("draws the state with its clock, a spinner only while live, and Open with its arrow", () => {
    const working = rest({ kind: "working", clock: "29h 28m" });
    expect(working).toMatch(/class="cv-spinner[^"]*"/);
    expect(working).toMatch(/Working · <span data-conversation-rest-clock[^>]*>29h 28m<\/span>/);
    expect(rest({ kind: "setting-up", clock: "1m 35s" })).toMatch(/Setting up · <span data-conversation-rest-clock[^>]*>1m 35s</);
    for (const [kind, words] of [["idle", "Idle"], ["offline", "Machine offline"]] as const) {
      const html = rest({ kind, clock: null });
      expect(html).toContain(`>${words}</span>`);
      expect(html).not.toContain("cv-spinner");
      expect(html).not.toContain("data-conversation-rest-clock");
    }
    const open = rest({ kind: "idle", clock: null });
    expect(open).toMatch(/<button[^>]*data-conversation-rest-bar/);
    expect(open).toMatch(/c2-rest-control-label[^>]*>Open<\/span>[\s\S]*?<svg class="c2-rest-control-arrow[^"]*" viewBox="0 0 16 16" data-conversation-open-arrow=""><path d="m4 10 4-4 4 4"><\/path><\/svg>/);
  });
  it("keeps the state while an ask is the composer under it, and gives the ask the preview's room", () => {
    const html = rest({ kind: "idle", clock: null }, { askBelow: true, preview: "Ready for review." });
    expect(html).toContain(">Idle</span>");
    expect(html).not.toContain("data-conversation-unread-preview");
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
