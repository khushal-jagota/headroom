import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import ConversationTranscript from "../src/components/conversation/ConversationTranscript.svelte";
import { rowForBody } from "../src/lib/conversation/view/presentation";

// The projected body is the only place a command's reply exists. SSR proves the body
// reaches the transcript as its own line, with every line of the reply kept.
describe("command reply", () => {
  it("draws the backend's reply as one quiet line holding its whole text", () => {
    const row = rowForBody(
      { key: "e2", source_sequence: 2, created_at: 2 },
      {
        kind: "command_reply",
        reply_text: "Goal: ship the fix\nactive · 0 tokens",
      },
    );
    const html = render(ConversationTranscript, {
      props: {
        rows: [],
        visibleRows: [],
        lens: "focus",
        conversationId: "c",
        projectedItems: [
          { kind: "row", key: row.key, row, behindTheFoldOf: null },
        ],
      },
    }).body;
    expect(html).toContain('data-conversation-row="command_reply"');
    expect(html).toContain("Goal: ship the fix\nactive · 0 tokens");
  });
});

// The live task list disappears when a task ends. These rows must survive as history.
it("keeps both background task lifecycle facts in the full transcript", () => {
  const rows = [
    rowForBody(
      { key: "e2", source_sequence: 2, created_at: 2 },
      {
        kind: "background_task_started",
        task_id: "t1",
        task_description: "Check tests",
      },
    ),
    rowForBody(
      { key: "e3", source_sequence: 3, created_at: 3 },
      {
        kind: "background_task_ended",
        task_id: "t1",
        task_description: "Check tests",
        task_status: "completed",
      },
    ),
  ];
  const html = render(ConversationTranscript, {
    props: {
      rows: [],
      visibleRows: [],
      lens: "full",
      conversationId: "c",
      projectedItems: rows.map((row) => ({
        kind: "row" as const,
        key: row.key,
        row,
        behindTheFoldOf: null,
      })),
    },
  }).body;
  expect(html).toContain("Background task started · Check tests");
  expect(html).toContain("Background task completed · Check tests");
});

// File-only answers have no answer text. Prove projection and rendering retain the actual
// stored file reference, rather than showing a blank completed answer.
describe("structured answer attachment", () => {
  it("draws a file-only answer as the existing file card with the conversation file address", () => {
    const row = rowForBody(
      { key: "answer", source_sequence: 4, created_at: 4 },
      {
        kind: "user_input",
        request_id: "request",
        state: "answered",
        dead_reason: null,
        failure_detail: null,
        answer_fate: "applied",
        questions: [
          {
            question_id: "proof",
            header: "Proof",
            question: "Show the evidence",
            options: [],
            multi_select: false,
            allow_other: true,
          },
        ],
        answers: [
          {
            question_id: "proof",
            answers: [],
            attachments: [
              {
                piece: "file",
                stored_file_id: "f_answer",
                file_name: "evidence.txt",
                media_type: "text/plain",
                byte_count: 13,
              },
            ],
          },
        ],
      },
    );
    const html = render(ConversationTranscript, {
      props: {
        rows: [],
        visibleRows: [],
        lens: "focus",
        conversationId: "question-conversation",
        projectedItems: [
          { kind: "row", key: row.key, row, behindTheFoldOf: null },
        ],
      },
    }).body;
    expect(html).toContain("evidence.txt");
    expect(html).toContain(
      "/conversations/question-conversation/files/f_answer",
    );
    expect(html).toContain("data-conversation-user-input-answers");
  });
});
