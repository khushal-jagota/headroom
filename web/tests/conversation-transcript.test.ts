import { describe, expect, it } from "vitest";

import {
  emptyConversationFeed,
  feedWithCommittedEvent,
  feedWithCommittedEvents,
  feedWithLiveFrame
} from "../src/lib/conversation/feed";
import {
  askDeadSentence,
  liveAskFrom,
  liveUserInputFrom,
  promptLabelFor,
  refusalSentence,
  transcriptRows,
  turnEndingSentence,
  type TranscriptRow
} from "../src/lib/conversation/transcript";
import type { ConversationEvent } from "../src/lib/conversation/wire";
import {
  agentMessageEvent,
  permissionAskedEvent,
  promptEvent,
  toolCallFinishedEvent,
  toolCallStartedEvent,
  turnEndedEvent
} from "./support/conversationEvents";

function rowsFrom(events: readonly ConversationEvent[]): TranscriptRow[] {
  return transcriptRows(feedWithCommittedEvents(emptyConversationFeed(), events));
}

function rowOfKind<Kind extends TranscriptRow["kind"]>(
  rows: readonly TranscriptRow[],
  kind: Kind
): Extract<TranscriptRow, { kind: Kind }> {
  const found = rows.find(
    (row): row is Extract<TranscriptRow, { kind: Kind }> => row.kind === kind
  );
  if (!found) throw new Error(`missing ${kind} transcript row`);
  return found;
}

describe("Conversation transcript", () => {
  it("labels only prompts from somebody other than the reader", () => {
    expect(promptLabelFor("owner", "owner")).toBeNull();
    expect(promptLabelFor("automatic loop", "owner")).toBe("automatic loop");
    expect(promptLabelFor("owner", null)).toBe("owner");
  });

  it("reconciles a tool start and finish into one completed row", () => {
    const rows = rowsFrom([
      promptEvent(1, "do it"),
      toolCallStartedEvent(2, { toolCallId: "tool-1", title: "Read file" }),
      toolCallFinishedEvent(3, {
        toolCallId: "tool-1",
        status: "completed",
        detail: "42 lines"
      })
    ]);
    const tools = rows.filter(
      (row): row is Extract<TranscriptRow, { kind: "tool_call" }> =>
        row.kind === "tool_call"
    );

    expect(tools).toHaveLength(1);
    expect(tools[0]).toMatchObject({
      toolCallId: "tool-1",
      title: "Read file",
      status: "completed",
      detail: "42 lines"
    });
    expect(rows[0]).toMatchObject({ kind: "prompt", mode: "run_when_free" });
  });

  it("keeps a running unanswered ask live", () => {
    const rows = rowsFrom([
      promptEvent(1),
      permissionAskedEvent(2, { askId: "ask-1", title: "Run ls" })
    ]);
    const ask = rowOfKind(rows, "permission_ask");

    expect(ask).toMatchObject({
      askId: "ask-1",
      title: "Run ls",
      state: "live",
      deadReason: null
    });
    expect(liveAskFrom(rows)).toBe(ask);
  });

  it("folds an answer into its ask using the backend's label", () => {
    const answered = {
      conversation_id: "c1",
      sequence: 3,
      kind: "permission_answered",
      payload: { ask_id: "ask-1", option_id: "allow" },
      created_at: 1_700_000_000
    } satisfies ConversationEvent;
    const rows = rowsFrom([
      promptEvent(1),
      permissionAskedEvent(2, {
        askId: "ask-1",
        title: "Run ls",
        options: [
          { option_id: "reject", label: "No", option_kind: "reject_once" },
          { option_id: "allow", label: "Yes", option_kind: "allow_once" }
        ]
      }),
      answered
    ]);
    const ask = rowOfKind(rows, "permission_ask");

    expect(ask).toMatchObject({
      state: "answered",
      answeredOptionLabel: "Yes",
      deadReason: null
    });
    expect(liveAskFrom(rows)).toBeNull();
  });

  it("expires an unanswered ask when its recorded turn ends", () => {
    const rows = rowsFrom([
      promptEvent(1),
      permissionAskedEvent(2, {
        askId: "ask-1",
        title: "Run rm",
        detail: "in /workspace"
      }),
      turnEndedEvent(3, { ending: "interrupted" })
    ]);
    const ask = rowOfKind(rows, "permission_ask");

    expect(ask).toMatchObject({
      state: "dead",
      deadReason: "turn_ended"
    });
    expect(askDeadSentence(ask.deadReason)).toBe("expired with the turn");
    expect(liveAskFrom(rows)).toBeNull();
  });

  it("expires asks and states when a turn stopped without an ending", () => {
    const feed = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1),
      permissionAskedEvent(2, { askId: "ask-1", title: "Run ls" })
    ]);
    const rows = transcriptRows(feed, { turnStoppedWithoutAnEnding: true });
    const ask = rowOfKind(rows, "permission_ask");

    expect(ask).toMatchObject({
      state: "dead",
      deadReason: "no_ending_recorded"
    });
    expect(askDeadSentence(ask.deadReason))
      .toBe("expired — its turn stopped without an ending");
    expect(liveAskFrom(rows)).toBeNull();
    expect(rows.some((row) => row.kind === "turn_stopped")).toBe(true);
  });

  it("reconstructs a pending multi-question request from durable rows", () => {
    const requested = {
      conversation_id: "c1",
      sequence: 2,
      kind: "user_input_requested",
      payload: {
        request_id: "input-1",
        questions: [
          {
            question_id: "scope",
            header: "Scope",
            question: "Which surfaces?",
            options: [{ label: "Web", description: "The browser app" }],
            multi_select: true,
            allow_other: true
          },
          {
            question_id: "tests",
            header: "Tests",
            question: "How much coverage?",
            options: [{ label: "Focused", description: "Regression coverage" }],
            multi_select: false,
            allow_other: false
          }
        ]
      },
      created_at: 1_700_000_000
    } satisfies ConversationEvent;
    const rows = rowsFrom([promptEvent(1), requested]);
    const pending = liveUserInputFrom(rows);

    expect(pending).toMatchObject({
      kind: "user_input",
      requestId: "input-1",
      state: "live"
    });
    expect(pending?.questions).toHaveLength(2);
  });

  it("folds a complete answer map into the request and stops offering it", () => {
    const requested = {
      conversation_id: "c1",
      sequence: 2,
      kind: "user_input_requested",
      payload: {
        request_id: "input-1",
        questions: [{
          question_id: "scope",
          header: "Scope",
          question: "Which surface?",
          options: [],
          multi_select: false,
          allow_other: true
        }]
      },
      created_at: 1_700_000_000
    } satisfies ConversationEvent;
    const answered = {
      conversation_id: "c1",
      sequence: 3,
      kind: "user_input_answered",
      payload: {
        request_id: "input-1",
        answers: { scope: { answers: ["The ticket pane"] } }
      },
      created_at: 1_700_000_001
    } satisfies ConversationEvent;
    const rows = rowsFrom([promptEvent(1), requested, answered]);
    const row = rowOfKind(rows, "user_input");

    expect(row).toMatchObject({
      state: "answered",
      answers: { scope: { answers: ["The ticket pane"] } }
    });
    expect(liveUserInputFrom(rows)).toBeNull();
  });

  it("keeps malformed delivery failures visible and expires abandoned requests", () => {
    const request = {
      conversation_id: "c1",
      sequence: 2,
      kind: "user_input_requested",
      payload: {
        request_id: "input-1",
        questions: [{
          question_id: "q1",
          header: "Choice",
          question: "Choose",
          options: [],
          multi_select: false,
          allow_other: true
        }]
      },
      created_at: 1_700_000_000
    } satisfies ConversationEvent;
    const failed = {
      conversation_id: "c1",
      sequence: 3,
      kind: "user_input_failed",
      payload: { request_id: "input-1", detail: "Malformed question payload" },
      created_at: 1_700_000_001
    } satisfies ConversationEvent;
    expect(rowOfKind(rowsFrom([failed]), "user_input")).toMatchObject({
      state: "failed",
      questions: [],
      failureDetail: "Malformed question payload"
    });

    const expired = rowOfKind(
      rowsFrom([request, turnEndedEvent(3, { ending: "interrupted" })]),
      "user_input"
    );
    expect(expired).toMatchObject({ state: "dead", deadReason: "turn_ended" });
  });

  it("replaces streaming text with its committed message", () => {
    let feed = feedWithCommittedEvent(emptyConversationFeed(), promptEvent(1));
    feed = feedWithLiveFrame(feed, {
      frame: "agent_message_delta",
      text_delta: "half"
    });

    expect(transcriptRows(feed).at(-1)).toMatchObject({
      kind: "streaming_agent_message",
      text: "half"
    });

    feed = feedWithCommittedEvent(feed, agentMessageEvent(2, "half a message, finished"));
    const rows = transcriptRows(feed);
    expect(rows.map((row) => row.kind)).toEqual(["prompt", "agent_message"]);
    expect(rowOfKind(rows, "agent_message").content)
      .toEqual([{ piece: "text", text: "half a message, finished" }]);
  });

  it("projects refused delivery and turn-ending wording", () => {
    const refused = {
      conversation_id: "c1",
      sequence: 1,
      kind: "prompt_delivery_refused",
      payload: {
        text: "held text",
        sender_label: "owner",
        mode: "run_when_free",
        refusal_reason: "backend_did_not_start"
      },
      created_at: 1_700_000_000
    } satisfies ConversationEvent;
    const row = rowOfKind(rowsFrom([refused]), "prompt_refused");

    expect(row).toMatchObject({
      reason: "backend_did_not_start",
      sentence: "the backend would not start"
    });
    expect(refusalSentence("backend_did_not_start")).toBe("the backend would not start");
    expect(turnEndingSentence("failed", "child died")).toBe("turn failed · child died");
    expect(turnEndingSentence("interrupted", null)).toBe("turn interrupted");
  });

  it("preserves image pieces on both sides and normalizes legacy text", () => {
    const content = [
      { piece: "text" as const, text: "look at this" },
      { piece: "image" as const, stored_file_id: "file-1", media_type: "image/png" }
    ];
    const events: ConversationEvent[] = [
      {
        conversation_id: "c1",
        sequence: 1,
        kind: "prompt",
        payload: { content, sender_label: "owner", mode: "run_when_free" },
        created_at: 1_700_000_000
      },
      {
        conversation_id: "c1",
        sequence: 2,
        kind: "agent_message",
        payload: { content },
        created_at: 1_700_000_000
      },
      agentMessageEvent(3, "only words")
    ];
    const rows = rowsFrom(events);

    expect(rowOfKind(rows, "prompt").content).toEqual(content);
    const agentRows = rows.filter(
      (row): row is Extract<TranscriptRow, { kind: "agent_message" }> =>
        row.kind === "agent_message"
    );
    expect(agentRows[0].content).toEqual(content);
    expect(agentRows[1].content).toEqual([{ piece: "text", text: "only words" }]);
  });

  it("retains usage and compaction rows with absent values represented as null", () => {
    const events: ConversationEvent[] = [
      {
        conversation_id: "c1",
        sequence: 1,
        kind: "token_usage",
        payload: {
          input_tokens: 41_000,
          output_tokens: 920,
          cached_input_tokens: 38_400,
          cost_usd: 0.42
        },
        created_at: 1_700_000_000
      },
      {
        conversation_id: "c1",
        sequence: 2,
        kind: "context_compacted",
        payload: {},
        created_at: 1_700_000_000
      },
      {
        conversation_id: "c1",
        sequence: 3,
        kind: "token_usage",
        payload: { input_tokens: 512 },
        created_at: 1_700_000_000
      }
    ];
    const rows = rowsFrom(events);
    const usage = rows.filter(
      (row): row is Extract<TranscriptRow, { kind: "token_usage" }> =>
        row.kind === "token_usage"
    );

    expect(rows.map((row) => row.kind))
      .toEqual(["token_usage", "context_compacted", "token_usage"]);
    expect(usage[0]).toMatchObject({
      inputTokens: 41_000,
      outputTokens: 920,
      cachedInputTokens: 38_400,
      costUsd: 0.42
    });
    expect(usage[1]).toMatchObject({
      inputTokens: 512,
      outputTokens: null,
      cachedInputTokens: null,
      costUsd: null
    });
  });
});
