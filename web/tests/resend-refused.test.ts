import { describe, expect, it, vi } from "vitest";
import { render } from "svelte/server";
import UserMessage from "../src/components/conversation/UserMessage.svelte";
import { promptOutcomeStatus, refusalOffersResend, type TranscriptRow } from "../src/lib/conversation/transcript";
import {
  ATTACHMENT_NOT_READ_BACK,
  RESEND_RECORDED_MESSAGE,
  recordedContentCanBeResent,
  sentPiecesFromRecorded,
  type ResendRecordedMessage
} from "../src/lib/conversation/resendRefused";
import type { MessagePiece } from "../src/lib/conversation/wire";

type PromptRow = Extract<TranscriptRow, { kind: "prompt_refused" }>;
const withFiles: MessagePiece[] = [
  { piece: "text", text: "Here is the log and the chart." },
  { piece: "file", stored_file_id: "f1", media_type: "text/plain", file_name: "ci-run.txt", byte_count: 5 },
  { piece: "image", stored_file_id: "i1", media_type: "image/png", file_name: "chart.png" }
];
const refused: PromptRow = {
  key: "e7", kind: "prompt_refused", sequence: 7, createdAt: 1, content: withFiles, senderLabel: "owner",
  senderMessageId: "m1", reason: "backend_did_not_start", sentence: "the backend did not start"
};
const drawWith = (row: PromptRow, context: ResendRecordedMessage | null) => render(UserMessage, {
  props: { row, conversationId: "c1", ownSenderLabel: "owner" },
  context: context ? new Map<symbol, unknown>([[RESEND_RECORDED_MESSAGE, context]]) : undefined
}).body;

describe("sending again a message that was not delivered", () => {
  it("reads each kept file back from its conversation and sends it with its bytes", async () => {
    const bytes: Record<string, Uint8Array<ArrayBuffer>> = {
      "/api/conversation/conversations/c1/files/f1": new Uint8Array([104, 101, 108, 108, 111]),
      "/api/conversation/conversations/c1/files/i1": new Uint8Array([137, 80, 78, 71])
    };
    const read = vi.fn(async (url: string | URL | Request) => new Response(bytes[String(url)]));
    const pieces = await sentPiecesFromRecorded("c1", withFiles, read as unknown as typeof fetch);
    expect(read).toHaveBeenCalledTimes(2);
    expect(pieces).toEqual([
      { piece: "text", text: "Here is the log and the chart." },
      { piece: "file", data: btoa("hello"), media_type: "text/plain", file_name: "ci-run.txt" },
      { piece: "image", data: btoa(String.fromCharCode(137, 80, 78, 71)), media_type: "image/png", file_name: "chart.png" }
    ]);
  });

  it("sends nothing when a kept file cannot be read back", async () => {
    const read = vi.fn(async () => new Response("gone", { status: 404 }));
    await expect(sentPiecesFromRecorded("c1", withFiles, read as unknown as typeof fetch)).rejects.toThrow(ATTACHMENT_NOT_READ_BACK);
  });

  it("is not offered for a video, which travels by upload rather than with the message", () => {
    expect(recordedContentCanBeResent(withFiles)).toBe(true);
    expect(recordedContentCanBeResent([{ piece: "file", stored_file_id: "v", media_type: "video/mp4", file_name: "a.mp4", byte_count: 9 }])).toBe(false);
  });

  it("is offered only for refusals that sending again can get past", () => {
    expect(refusalOffersResend("backend_did_not_start")).toBe(true);
    expect(refusalOffersResend("backend_rejected_prompt")).toBe(false);
    expect(refusalOffersResend("no_such_conversation")).toBe(false);
    expect(promptOutcomeStatus(refused, true).next).toBe("Resend it, or choose another model in the composer first.");
  });

  it("shows Resend on the refused message, at rest, and names it in the next step", async () => {
    const resend = vi.fn(async () => true);
    const html = drawWith(refused, { available: () => true, resend });
    expect(html).toContain('data-conversation-resend-refused="e7"');
    expect(html).toContain(">Resend</button>");
    // v8 (9): the card says the cause; its buttons say the rest.
    expect(html).toContain("The backend did not start.");
    expect(html).not.toContain("Resend it, or choose another model in the composer first.");
  });

  it("does not show Resend in a read-only pane, nor for a message that cannot be sent again", () => {
    expect(drawWith(refused, { available: () => false, resend: async () => true })).not.toContain("data-conversation-resend-refused");
    expect(drawWith({ ...refused, reason: "backend_rejected_prompt" }, { available: () => true, resend: async () => true }))
      .not.toContain("data-conversation-resend-refused");
    expect(drawWith(refused, null)).not.toContain("data-conversation-resend-refused");
  });
});
