import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import MessagePieces from "../src/components/conversation/MessagePieces.svelte";
import type { MessagePiece } from "../src/lib/conversation/wire";

const draw = (content: MessagePiece[]): string => render(MessagePieces, { props: { bubble: true, conversationId: "c", content } }).body;
const csv: MessagePiece = { piece: "file", stored_file_id: "f1", file_name: "export.csv", media_type: "text/csv", byte_count: 1700 };

describe("a user bubble with attachments", () => {
  it("holds its attachments inside the one bubble, above its words", () => {
    const html = draw([{ piece: "text", text: "Use this export." }, csv]);
    expect(html.match(/data-conversation-bubble[ =>]/g)?.length).toBe(1);
    expect(html.indexOf("conversation-file-card")).toBeLessThan(html.indexOf("c2-bubble-words"));
    expect(draw([{ piece: "text", text: "Hi" }])).not.toContain("c2-bubble--attached");
  });

  it("is as wide as its picture when one picture is all it holds; a file card does not do that", () => {
    expect(draw([
      { piece: "image", stored_file_id: "i1", file_name: "chart.png", media_type: "image/png" },
      { piece: "text", text: "Match this." }
    ])).toContain("c2-bubble--one-image");
    expect(draw([csv, { piece: "text", text: "Use this." }])).not.toContain("c2-bubble--one-image");
  });
});
