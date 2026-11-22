import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import UserMessage from "../src/components/conversation/UserMessage.svelte";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import TranscriptStatusRow from "../src/components/conversation/TranscriptStatusRow.svelte";
import { promptOutcomeStatus, turnEndingStatus, turnFailureSentence, TURN_STOPPED_STATUS, type TranscriptRow } from "../src/lib/conversation/transcript";

type PromptRow = Extract<TranscriptRow, { kind: "prompt" | "prompt_refused" | "prompt_uncertain" | "prompt_discarded" }>;
const words = [{ piece: "text" as const, text: "Are you still on this?" }];
const base = { key: "e1", sequence: 1, createdAt: 1, content: words, senderLabel: "owner", senderMessageId: null };
const draw = (row: PromptRow) => render(UserMessage, { props: { row, conversationId: "c", ownSenderLabel: "owner" } }).body;

describe("a turn that failed", () => {
  it("leads with a plain sentence and keeps the backend's words beneath it", () => {
    const raw = "stream disconnected before completion: rate limit reached for gpt-6.1-sol (resets in 41m)";
    expect(turnEndingStatus("failed", raw)).toEqual({
      tone: "error",
      title: "The turn stopped because the model's rate limit was reached.",
      detail: raw,
      next: "The provider said the limit resets in 41m. Send a message after that, or choose another model in the composer.",
      detailOpen: false
    });
    expect(turnFailureSentence("codex app-server exited with code 1")).toBe("The turn stopped because the agent's process exited.");
    expect(turnFailureSentence("stream disconnected before completion")).toBe("The turn stopped because the connection to the model dropped.");
    expect(turnFailureSentence("something nobody has seen")).toBe("The turn stopped because of an error.");
    expect(turnEndingStatus("failed", null)?.detail).toBeNull();
    const html = render(TranscriptStatusRow, { props: { status: turnEndingStatus("failed", raw)!, role: "alert" } }).body;
    expect(html).toContain("c2-status--error");
    expect(html).toContain("data-conversation-status-detail");
    expect(html).toContain(raw);
  });

  it("says the next step, and keeps the backend's words behind a disclosure", () => {
    const raw = "stream disconnected before completion: rate limit reached for gpt-6.1-sol (resets in 41m)";
    const html = render(TranscriptStatusRow, { props: { status: turnEndingStatus("failed", raw)!, role: "alert" } }).body;
    expect(html).toContain("data-conversation-status-next");
    expect(html).toContain("resets in 41m. Send a message after that");
    expect(html).toMatch(/<details class="c2-status-more[^"]*"(?![^>]*\bopen\b)[^>]*>/);
    expect(html).toContain("Details");
    // A rate limit that does not say when it lifts still has a next step.
    expect(turnEndingStatus("failed", "429 Too Many Requests")?.next).toBe("Send a message once the limit resets, or choose another model in the composer.");
    expect(turnEndingStatus("failed", "rate limit exceeded; resets at 3:10 PM UTC")?.next).toContain("resets at 3:10 PM UTC.");
  });

  it("opens the backend's words when the sentence could not name a cause", () => {
    const status = turnEndingStatus("failed", "something nobody has seen")!;
    expect(status.title).toBe("The turn stopped because of an error.");
    expect(status.next).toBe("Send a message to try again.");
    expect(status.detailOpen).toBe(true);
    const html = render(TranscriptStatusRow, { props: { status } }).body;
    expect(html).toMatch(/<details class="c2-status-more[^"]*"[^>]*\bopen\b/);
  });

  it("draws an interrupted turn and a turn with no ending as stopped, a step above metadata", () => {
    expect(turnEndingStatus("interrupted", null)).toEqual({ tone: "stopped", title: "Turn interrupted", detail: null });
    expect(turnEndingStatus("completed", null)).toBeNull();
    expect(TURN_STOPPED_STATUS.tone).toBe("stopped");
    const html = render(TranscriptStatusRow, { props: { status: turnEndingStatus("interrupted", null)! } }).body;
    expect(html).toContain("c2-status--stopped");
    expect(html).not.toContain("data-conversation-status-detail");
    expect(html).not.toContain("data-conversation-status-next");
  });
});

describe("a message that was not delivered", () => {
  it("keeps the person's words in the bubble and the failure under it", () => {
    const sentence = "codex app-server exited with code 1 before it accepted the prompt";
    expect(promptOutcomeStatus({ kind: "prompt_refused", reason: "backend_did_not_start", sentence }))
      .toEqual({ tone: "error", title: "Not delivered: the backend would not start.", detail: sentence,
        next: "Send it again to retry, or choose another model in the composer." });
    expect(promptOutcomeStatus({ kind: "prompt_refused", reason: "backend_rejected_steer", sentence: "" }).next)
      .toBe("Send it again with Queue, so it goes in after this turn.");
    expect(promptOutcomeStatus({ kind: "prompt_refused", reason: "no_such_conversation", sentence: "" }).next).toBeNull();
    expect(promptOutcomeStatus({ kind: "prompt_refused", reason: "backend_did_not_start", sentence: "the backend would not start" }).detail).toBeNull();
    const html = draw({ ...base, kind: "prompt_refused", reason: "backend_did_not_start", sentence });
    const bubble = html.slice(html.indexOf("data-conversation-bubble"), html.indexOf("data-conversation-message-status"));
    expect(bubble).toContain("markdown-host");
    expect(bubble).not.toContain("Not delivered");
    expect(html).toContain('data-conversation-message-status="error"');
    expect(html).toContain('role="alert"');
  });
  it("ends on the bubble's edge, with its mark on the sentence's line", () => {
    const sentence = "codex app-server exited with code 1 before it accepted the prompt";
    const html = draw({ ...base, kind: "prompt_refused", reason: "backend_did_not_start", sentence });
    const status = html.slice(html.indexOf("data-conversation-message-status"));
    expect(html).toContain("c2-status--end");
    const line = status.slice(status.indexOf("c2-status-line"), status.indexOf("data-conversation-status-detail"));
    expect(line).toContain("c2-status-mark");
    expect(line).toContain("Not delivered");
    expect(status).toContain("Send it again to retry");
  });
  it("marks an ordinary message with nothing", () => {
    const html = draw({ ...base, kind: "prompt", mode: "queue", sentAtUnixMilliseconds: 1000 });
    expect(html).not.toContain("data-conversation-message-status");
    expect(html).not.toContain("data-conversation-steered");
  });
});

describe("a message that steered a running turn", () => {
  it("says quietly that it joined the turn", () => {
    const html = draw({ ...base, kind: "prompt", mode: "steer", sentAtUnixMilliseconds: 1000 });
    expect(html).toContain("data-conversation-steered");
    expect(html).toContain("Steered into the running turn");
  });
});

describe("a message with attachments", () => {
  it("puts only the words in the bubble and the file beside it", () => {
    const html = draw({ ...base, kind: "prompt", mode: "queue", sentAtUnixMilliseconds: 1000, content: [
      ...words, { piece: "file", stored_file_id: "f1", file_name: "ci.txt", media_type: "text/plain", byte_count: 10 } as never
    ] });
    const bubble = html.slice(html.indexOf("data-conversation-bubble"));
    const bubbleEnd = bubble.indexOf("</div>");
    expect(bubble.slice(0, bubbleEnd)).not.toContain("conversation-file-card");
    expect(html).toContain("attaches-files");
  });
});

describe("a running turn's head", () => {
  it("separates its clock and its count the way a settled head does", () => {
    const html = render(TurnAnchor, { props: { settled: false, startedAtUnixMilliseconds: Date.now(), toolCallCount: 3, onToggle: () => {} } }).body;
    expect(html).toMatch(/Working[\s\S]*·[\s\S]*3 tool calls/);
  });
});
