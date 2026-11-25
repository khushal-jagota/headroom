import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import MessagePieces from "../src/components/conversation/MessagePieces.svelte";
import type { MessagePiece } from "../src/lib/conversation/wire";
import type { LocalMediaPiece } from "../src/lib/conversation/localSentMedia";

const draw = (content: (MessagePiece | LocalMediaPiece)[]): string => render(MessagePieces, { props: { bubble: true, conversationId: "c", content } }).body;
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

  // Distinct risk: a picture's box is sized only after it loads, so the message changes
  // size when a sent picture arrives. The box must be in the markup before any load.
  it("draws a picture's final box before it loads, and the sent copy makes the same bubble", () => {
    const sized = draw([{ piece: "image", stored_file_id: "i1", file_name: "photo.png", media_type: "image/png", width: 1200, height: 800 }]);
    expect(sized).toContain('data-media-frame="sized"');
    expect(sized).toContain("--media-w: 1200px; --media-ratio: 1200 / 800");
    expect(draw([{ piece: "image", stored_file_id: "i1", file_name: "old.png", media_type: "image/png" }])).toContain('data-media-frame="fixed"');
    const local = draw([{ piece: "local_media", kind: "image", url: "blob:photo", name: "photo.png", size: { width: 1200, height: 800 }, progress: 40 }]);
    expect(local).toContain("c2-bubble--one-image");
    expect(local).toContain("--media-w: 1200px; --media-ratio: 1200 / 800");
    expect(local).not.toContain("Attached file");
  });
});

