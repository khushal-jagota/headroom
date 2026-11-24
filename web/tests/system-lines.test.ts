import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import UserMessage from "../src/components/conversation/UserMessage.svelte";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import TranscriptStatusRow from "../src/components/conversation/TranscriptStatusRow.svelte";
import { promptOutcomeStatus, statusReason, turnEndingStatus, turnFailureSentence, TURN_STOPPED_STATUS, type TranscriptRow } from "../src/lib/conversation/transcript";
import { SvelteSet } from "svelte/reactivity";
import ProjectedTranscript from "../src/components/conversation/view/ProjectedTranscript.svelte";
import type { ConversationViewItem, ConversationViewStatus } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

// Every system and lifecycle line is the one SystemLine, no line is a card, and a message
// that was not delivered says so inside its own bubble.

type PromptRow = Extract<TranscriptRow, { kind: "prompt" | "prompt_refused" | "prompt_uncertain" | "prompt_discarded" }>;
const words = [{ piece: "text" as const, text: "Are you still on this?" }];
const base = { key: "e1", sequence: 1, createdAt: 1, content: words, senderLabel: "owner", senderMessageId: null };
const draw = (row: PromptRow, ownSenderLabel = "owner") => render(UserMessage, { props: { row, conversationId: "c", ownSenderLabel } }).body;
const rateLimited = "stream disconnected before completion: rate limit reached for gpt-6.1-sol (resets in 41m)";

describe("what a status says", () => {
  it("names a failed turn's cause in plain words and keeps the backend's own beneath", () => {
    expect(turnEndingStatus("failed", rateLimited)).toEqual({
      tone: "error",
      title: "The turn stopped because the model's rate limit was reached.",
      detail: rateLimited,
      next: "The provider said the limit resets in 41m. Send a message after that, or choose another model.",
      action: "choose-model",
      brief: "The model's rate limit was reached. It resets in 41m.",
      detailOpen: false
    });
    expect(turnFailureSentence("codex app-server exited with code 1")).toBe("The turn stopped because the agent's process exited.");
    expect(turnFailureSentence("something nobody has seen")).toBe("The turn stopped because of an error.");
    expect(turnEndingStatus("failed", "something nobody has seen")?.detailOpen).toBe(true);
    expect(turnEndingStatus("failed", "429 Too Many Requests")?.next).toBe("Send a message once the limit resets, or choose another model.");
  });

  it("gives the line its reason as one short phrase", () => {
    expect(statusReason(turnEndingStatus("failed", rateLimited)!)).toBe("The model's rate limit was reached. It resets in 41m");
    expect(statusReason(turnEndingStatus("failed", "stream disconnected before completion")!)).toBe("The connection to the model dropped");
    expect(statusReason(turnEndingStatus("failed", "something nobody has seen")!)).toBe("An error");
    expect(statusReason(promptOutcomeStatus({ kind: "prompt_refused", reason: "backend_did_not_start", sentence: "" }))).toBe("The backend did not start");
  });

  it("says an interrupted turn and a turn with no ending the same way", () => {
    expect(turnEndingStatus("interrupted", null)).toEqual({ tone: "stopped", title: "Stopped before it finished", detail: null });
    // With its turn's length known, the stopped turn's one line says it (its head is not drawn).
    expect(turnEndingStatus("interrupted", null, 525)).toEqual({ tone: "stopped", title: "Stopped after 8m 45s", detail: null });
    expect(TURN_STOPPED_STATUS.title).toBe("Stopped before it finished");
    expect(turnEndingStatus("completed", null)).toBeNull();
  });
});

describe("a turn's status line", () => {
  it("a failed turn is one line: the red dot, 'This turn failed · reason', then its text actions", () => {
    const html = render(TranscriptStatusRow, { props: { status: turnEndingStatus("failed", rateLimited)!, role: "alert" } }).body;
    expect(html).toContain('data-conversation-system-line="start"');
    expect(html).toContain('data-conversation-system-mark="failure"');
    expect(html).toContain("is-hanging");
    expect(html).toMatch(/This turn failed<span aria-hidden="true">( · )<\/span><span data-conversation-status-next="">The model's rate limit was reached\. It resets in 41m<\/span>/);
    // The real next step comes before Details; the backend's words wait closed behind Details.
    expect(html.indexOf("data-conversation-status-choose-model")).toBeGreaterThan(-1);
    expect(html.indexOf("data-conversation-status-choose-model")).toBeLessThan(html.indexOf("data-conversation-status-details"));
    expect(html).toMatch(/aria-expanded="false"[^>]*data-conversation-status-details/);
    expect(html).toMatch(/class="c2-backend-words[^"]*"[^>]*\bhidden\b/);
    expect(html.indexOf("data-conversation-status-copy")).toBeGreaterThan(html.indexOf("data-conversation-status-detail"));
    // No card, no pill buttons.
    expect(html).not.toMatch(/callout|c2-status-main|c2-status-second|cv-button/);
  });

  it("opens the backend's words at once when they are the only explanation", () => {
    const html = render(TranscriptStatusRow, { props: { status: turnEndingStatus("failed", "something nobody has seen")! } }).body;
    expect(html).toMatch(/aria-expanded="true"/);
    expect(html).toMatch(/<div class="c2-backend-words[^"]*"(?![^>]*\bhidden\b)[^>]*>/);
  });

  it("a stopped turn is one line with the grey dot and no actions", () => {
    const html = render(TranscriptStatusRow, { props: { status: TURN_STOPPED_STATUS } }).body;
    expect(html).toContain('data-conversation-system-mark="dot"');
    expect(html).toContain("Stopped before it finished");
    expect(html).not.toContain("cv-system-line-actions");
    expect(html).not.toContain("data-conversation-status-detail");
  });

  it("the turn head is the same line: 'Worked for', and 'Working · timer' with the spinner", () => {
    const settled = render(TurnAnchor, { props: { settled: true, durationSeconds: 110 } }).body;
    expect(settled).toContain('data-conversation-system-line="start"');
    expect(settled).not.toContain("data-conversation-system-mark");
    const running = render(TurnAnchor, { props: { settled: false, startedAtUnixMilliseconds: Date.now() - 62_000 } }).body;
    expect(running).toContain('data-conversation-system-mark="spinner"');
    expect(running).toContain('data-conversation-alive-word="">Working</span>');
  });
});

describe("a message's system lines", () => {
  it("a message that was not delivered says so inside its bubble, under a rule, with its actions", () => {
    const sentence = "codex app-server exited with code 1 before it accepted the prompt";
    const html = draw({ ...base, kind: "prompt_refused", reason: "backend_did_not_start", sentence });
    const bubble = html.slice(html.indexOf("data-conversation-bubble"));
    expect(html).toMatch(/class="c2-bubble[^"]*is-failed/);
    expect(bubble.indexOf("Are you still on this?")).toBeLessThan(bubble.indexOf("c2-undelivered"));
    expect(bubble).toMatch(/Not delivered<span aria-hidden="true"> · <\/span>The backend did not start/);
    expect(bubble).toContain('role="alert"');
    expect(bubble.indexOf("data-conversation-status-choose-model")).toBeLessThan(bubble.indexOf("data-conversation-status-details"));
    // Details wears the same chevron as the failed turn's Details (hands-on test 1, item 18).
    expect(bubble).toMatch(/data-conversation-status-details=""[^>]*>Details<svg class="c2-details-chevron/);
    // No separate card, no "!" beside the bubble.
    expect(html).not.toMatch(/callout|has-callout|undelivered"/);
    expect(html).not.toContain("data-conversation-system-line");
  });

  it("a caption with nothing to offer has no actions slot, so it ends at the bubble's edge", () => {
    // Hands-on test 1, item 7: an empty slot kept its 16 px of gap after the words.
    for (const kind of ["prompt_uncertain", "prompt_discarded"] as const) {
      const html = draw({ ...base, kind });
      expect(html).toContain('data-conversation-system-line="end"');
      expect(html).not.toContain("cv-system-line-actions");
    }
  });

  it("a message the offline Mac turned away is not delivered, in its bubble, with Resend and the server's words", () => {
    const local = { messageId: "m1", content: words, senderLabel: "owner", mode: "queue" as const, sentAtUnixMilliseconds: 1,
      knownFate: "machine_offline" as const, refusal: { reason: "The Mac is offline", detail: "Mac Tickets cannot start: no compatible Mac executor has leased yet" } };
    const html = render(UserMessage, { props: { local, conversationId: "c", ownSenderLabel: "owner" } }).body;
    expect(html).toMatch(/class="c2-bubble[^"]*is-failed/);
    expect(html).toMatch(/Not delivered<span aria-hidden="true"> · <\/span>The Mac is offline/);
    expect(html).toMatch(/data-conversation-held-send-again="m1"[^>]*>Resend</);
    expect(html).toContain("Mac Tickets cannot start");
  });

  it("an ordinary message has no line; a steered one has one caption on the bubble's edge", () => {
    expect(draw({ ...base, kind: "prompt", mode: "queue", sentAtUnixMilliseconds: 1000 })).not.toContain("data-conversation-system-line");
    const steered = draw({ ...base, kind: "prompt", mode: "steer", sentAtUnixMilliseconds: 1000 });
    expect(steered).toMatch(/data-conversation-system-line="end"[^>]*data-conversation-steered=""/);
    expect(steered).toMatch(/Steered<span aria-hidden="true"> · <\/span><span data-conversation-status-next="">into the running turn</);
  });

  it("a message from Panels is a left-aligned line with no pill", () => {
    const html = draw({ ...base, kind: "prompt", mode: "queue", sentAtUnixMilliseconds: 1000, senderLabel: "Panels" });
    expect(html).toMatch(/data-conversation-system-line="start"[^>]*data-conversation-system-note=""/);
    expect(html).toContain("From Panels");
    expect(html).not.toContain("c2-bubble");
  });

  it("a note from another sender says who sent it, with no system mark", () => {
    const html = draw({ ...base, kind: "prompt", mode: "queue", sentAtUnixMilliseconds: 1000, senderLabel: "Chief of Staff", content: [{ piece: "text", text: "Look at the flaky test first." }] });
    expect(html).toContain("From Chief of Staff");
    expect(html).not.toContain("data-conversation-system-mark");
  });
});

// The hover row (time and Copy) always stands 4 px under its message and is 24 px tall, so
// whatever follows a message stands at least a turn's gap (32) below it: the row never
// covers the next item, whatever that item is.
describe("the room under a message for its hover row", () => {
  const message = (key: string, sequence: number, sender: string | null): ConversationViewItem => ({
    kind: "row", key, row_kind: sender ? "prompt" : "agent_message", sender_message_id: sender, source_sequence: sequence,
    order: { sequence, slot: 0 }, changed_through_sequence: sequence, created_at: 1, turn_key: null, work_group_key: null,
    fold_rule: "never", body: sender
      ? { kind: "inline", value: { kind: "prompt", content: [{ piece: "text", text: key }], sender_label: "owner", mode: "steer" } }
      : { kind: "inline", value: { kind: "agent_message", content: [{ piece: "text", text: key }], to_owner: true } }
  } as ConversationViewItem);
  function rhythms(items: ConversationViewItem[], status: Partial<ConversationViewStatus> = {}) {
    const reader = {
      id: "c", lens: "full", coverage: 9, entryPhase: "ready", restorationAnchor: null, restorationAttempt: 0,
      pages: [{ conversation_id: "c", protocol_version: 1, projection_version: 1, lens: "full", scope: { kind: "timeline", key: null }, items, older: null, newer: null, body_bytes: 0 }],
      status: { turn_key: null, pending_permission: null, pending_user_input: null, ...status }, discardedBodies: 0, openTools: new SvelteSet<string>(),
      isExpanded: () => false, expand: async () => {}, load: async () => {}, textBodyReady: () => {}, failTextRestoration: () => {}, body: async () => "", receipts: async () => ({ receipts: [] })
    } as unknown as ConversationViewReader;
    const html = render(ProjectedTranscript, { props: { reader, models: [], ownSenderLabel: "owner", livenessPulse: 0 } }).body;
    return { html, gaps: [...html.matchAll(/data-conversation-view-key="([^"]+)"[^>]*data-rhythm="([^"]+)"/g)].map(([, key, rhythm]) => `${key}:${rhythm}`) };
  }
  it("puts a turn's gap after every message, also between two agent messages", () => {
    const { gaps, html } = rhythms([message("e1", 1, "m1"), message("e2", 2, null), message("e3", 3, null), message("e4", 4, "m4")]);
    expect(gaps).toEqual(["e1:first", "e2:turn", "e3:turn", "e4:turn"]);
    // The live edge drawn after the transcript reads that it ends with a message.
    expect(html).toContain("data-conversation-ends-with-message");
  });
  it("gives the line drawn after the last message the same gap", () => {
    const { html } = rhythms([message("e1", 1, "m1")], { stopped_without_ending: true });
    expect(html).toMatch(/data-conversation-row="turn_stopped"[^>]*data-rhythm="turn"|data-rhythm="turn"[^>]*data-conversation-row="turn_stopped"/);
    expect(html).not.toContain("data-conversation-ends-with-message");
  });
});
