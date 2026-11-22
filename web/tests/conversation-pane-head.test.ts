import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import { awaitsCredential, paneFailureFrom, paneHeadStatus, paneTitleFor, uncertainActivityFeedback } from "../src/lib/conversation/dockPresentation";
import type { ConversationViewItem, CredentialRequestRowBody } from "../src/lib/conversation/view/contracts";
import type { TranscriptRow } from "../src/lib/conversation/transcript";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";

const quiet = { readOnly: false, activityUncertain: false, waitingForYou: false, running: false, backgroundWorking: false, settingUp: null, elapsed: null };

describe("the opened pane's top bar", () => {
  it("names who the conversation is with, from the page or else the role", () => {
    expect(paneTitleFor("Vylo pricing and launch", "Manager")).toBe("Vylo pricing and launch");
    expect(paneTitleFor("  ", "Chief")).toBe("Chief of Staff");
    expect(paneTitleFor(null, "Worker")).toBe("Worker");
  });

  it("says what the conversation is doing, the person's turn first", () => {
    expect(paneHeadStatus(quiet)).toBeNull();
    expect(paneHeadStatus({ ...quiet, running: true, elapsed: "2m 14s" })).toEqual({ text: "Working · 2m 14s", tone: "live" });
    expect(paneHeadStatus({ ...quiet, running: true })).toEqual({ text: "Working", tone: "live" });
    expect(paneHeadStatus({ ...quiet, running: true, waitingForYou: true })).toEqual({ text: "Waiting for you", tone: "attention" });
    expect(paneHeadStatus({ ...quiet, backgroundWorking: true })?.tone).toBe("live");
    expect(paneHeadStatus({ ...quiet, settingUp: "Setting up · preparing workspace · 1m 35s" }))
      .toEqual({ text: "Setting up · preparing workspace · 1m 35s", tone: "live" });
    expect(paneHeadStatus({ ...quiet, running: true, activityUncertain: true })?.tone).toBe("quiet");
    expect(paneHeadStatus({ ...quiet, running: true, readOnly: true })?.text).toBe("Past conversation · read only");
  });

  it("says a failure honestly, from what last happened, and lets anything moving speak over it", () => {
    expect(paneFailureFrom({ lastBoundaryFailed: true, latestKind: "turn_ended" })).toBe("turn_failed");
    // A refused message is the newest thing; the failed turn before it is not repeated.
    expect(paneFailureFrom({ lastBoundaryFailed: false, latestKind: "prompt_refused" })).toBe("not_delivered");
    expect(paneFailureFrom({ lastBoundaryFailed: true, latestKind: "prompt_refused" })).toBe("not_delivered");
    // A message sent after a failure is the person moving on.
    expect(paneFailureFrom({ lastBoundaryFailed: false, latestKind: "prompt" })).toBeNull();
    expect(paneFailureFrom({ lastBoundaryFailed: false, latestKind: "prompt_uncertain" })).toBeNull();
    expect(paneHeadStatus({ ...quiet, failure: "turn_failed" })).toEqual({ text: "Last turn failed", tone: "failed" });
    expect(paneHeadStatus({ ...quiet, failure: "not_delivered" })).toEqual({ text: "Message not delivered", tone: "failed" });
    expect(paneHeadStatus({ ...quiet, failure: "turn_failed", running: true })?.tone).toBe("live");
    expect(paneHeadStatus({ ...quiet, failure: "turn_failed", waitingForYou: true })?.tone).toBe("attention");
    expect(paneHeadStatus({ ...quiet, failure: "turn_failed", backgroundWorking: true })?.tone).toBe("live");
    expect(paneHeadStatus({ ...quiet, failure: "turn_failed", readOnly: true })?.tone).toBe("quiet");
  });
});

describe("setting up while the machine cannot be read", () => {
  const workerSetup = { step: "preparing_workspace", began_at: 1, machine_id: "machine" } as const;
  it("reads as one state: the setup, still, with one line saying why it is unconfirmed", () => {
    expect(uncertainActivityFeedback(true)).toBe("This machine is not connected, so setup progress cannot be confirmed.");
    expect(uncertainActivityFeedback(false)).toBe("Current activity on this machine is unknown.");
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, workerSetup, activityKnown: false,
      feedback: uncertainActivityFeedback(true) } }).body;
    expect(html).toContain("Setting up · preparing workspace");
    expect(html).toContain("data-unconfirmed");
    expect(html.match(/data-conversation-dock-feedback/g)).toHaveLength(1);
    // A warning, not a spinner: no moving ring beside it, and the line under it in the warning tone.
    expect(html).not.toContain("c2-mark--running");
    expect(html).toMatch(/c2-rest-feedback[^"]*is-warning/);
  });
  it("keeps the moving mark while setup is known", () => {
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, workerSetup } }).body;
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
