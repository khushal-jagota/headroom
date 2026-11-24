import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import { conversationState, copyText, dockStatusFrom, type ConversationStateFacts, type DockStatus, interruptedFrom, paneFailureFrom, paneHeadStatus, paneTitleFor } from "../src/lib/conversation/dockPresentation";
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
    expect(paneHeadStatus({ ...quiet, settingUp: "Setting up · 1m 35s" }))
      .toMatchObject({ state: "setting-up", text: "Setting up · 1m 35s", mark: "spinner" });
    // A machine Panels cannot read says the dock's words, with a still dot and no clock,
    // also while its setup is recorded (owner, A4 in the opened view).
    for (const facts of [{ running: true }, { settingUp: "Setting up · 1m 35s" }, { waitingForYou: true }]) {
      expect(paneHeadStatus({ ...quiet, ...facts, activityUncertain: true }))
        .toEqual({ state: "offline", text: "Machine offline", mark: "dot" });
    }
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
    const setup = "Setting up · 1m 35s";
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
  const status = (facts: Partial<ConversationStateFacts>, offline = false, askWaiting = false) =>
    dockStatusFrom({ ...clocks, offline, askWaiting, conversation: { ...quiet, ...facts } });
  it("says one of four states, whatever else waits", () => {
    expect(status({ settingUp: "Setting up · 1m 35s" })).toEqual({ kind: "setting-up", clock: "1m 35s" });
    expect(status({ running: true })).toEqual({ kind: "working", clock: "2m 14s" });
    expect(status({})).toEqual({ kind: "idle", clock: null });
    // An ask, a proposal, a failure or a stop says itself elsewhere; the dock says Idle.
    expect(status({ running: true, waitingForYou: true, waitingFor: "answer" }, false, true)).toEqual({ kind: "idle", clock: null });
    for (const facts of [{ waitingForYou: true, waitingFor: "approval" as const },
      { failure: "turn_failed" as const }, { failure: "not_delivered" as const }, { interrupted: true }]) {
      expect(status(facts)).toEqual({ kind: "idle", clock: null });
    }
    // A proposal on the page does not stop the agent: a running turn still says Working.
    expect(status({ running: true, waitingForYou: true, waitingFor: "approval" })).toEqual({ kind: "working", clock: "2m 14s" });
  });
  it("says only Machine offline while the machine cannot be read, setting up or not", () => {
    expect(status({ activityUncertain: true, settingUp: "Setting up" }, true)).toEqual({ kind: "offline", clock: null });
    expect(status({ activityUncertain: true, running: true }, true)).toEqual({ kind: "offline", clock: null });
  });
  const rest = (status: DockStatus, props: Record<string, unknown> = {}) =>
    render(ConversationRestBar, { props: { onOpen: () => {}, status, ...props } }).body.replace(/<!--[^]*?-->/g, "");
  it("draws the state with its clock and no narration, a spinner only while live, and Open with its arrow", () => {
    const working = rest({ kind: "working", clock: "29h 28m" });
    expect(working).toContain('data-conversation-system-mark="spinner"');
    expect(working).toMatch(/Working · <span data-conversation-rest-clock[^>]*>29h 28m<\/span>/);
    // No narration sentence under the Working line.
    expect(working).not.toContain("narration");
    expect(rest({ kind: "setting-up", clock: "1m 35s" })).toMatch(/Setting up · <span data-conversation-rest-clock[^>]*>1m 35s</);
    for (const [kind, words] of [["idle", "Idle"], ["offline", "Machine offline"]] as const) {
      const html = rest({ kind, clock: null });
      expect(html).toContain(`>${words}</span>`);
      expect(html).toContain('data-conversation-system-mark="dot"');
      expect(html).not.toContain('data-conversation-system-mark="spinner"');
      expect(html).not.toContain("data-conversation-rest-clock");
    }
    // The status is the shared system line, its mark after the words so they start at the column's edge.
    expect(rest({ kind: "idle", clock: null })).toMatch(/data-conversation-rest-line[^]*cv-system-line-words[^]*cv-system-line-mark/);
    // The line's text is exactly its words: no spaces around them from the markup.
    const line = working.slice(working.indexOf("<div class=\"cv-system-line"), working.indexOf("</div></div>") + 12).replace(/<[^>]+>/g, "");
    expect(line).toBe("Working · 29h 28m");
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

describe("the options menu's workspace fact", () => {
  it("copies the path, and says Copied only when the clipboard took it", async () => {
    const written: string[] = [];
    expect(await copyText("/Users/k/panels", { writeText: async (text: string) => { written.push(text); } })).toBe(true);
    expect(written).toEqual(["/Users/k/panels"]);
    expect(await copyText("/x", { writeText: async () => { throw new Error("denied"); } })).toBe(false);
    expect(await copyText("/x", undefined)).toBe(false);
  });
});
