import { describe, expect, it } from "vitest";

import {
  COMMITTED_EVENT_STREAM_NAME,
  CONVERSATION_BACKEND_KEYS,
  CONVERSATION_BASE,
  LIVE_FRAME_STREAM_NAME,
  backendSupportsSteer,
  conversationFileHref,
  messageContentOf,
  messageContentText
} from "../src/lib/conversation/wire";

describe("Conversation wire values", () => {
  it("keeps the closed backend and stream vocabulary", () => {
    expect(CONVERSATION_BASE).toBe("/api/conversation");
    expect(COMMITTED_EVENT_STREAM_NAME).toBe("conversation-event");
    expect(LIVE_FRAME_STREAM_NAME).toBe("conversation-frame");
    expect(CONVERSATION_BACKEND_KEYS).toEqual(["hermes", "codex", "claude"]);
  });

  it("states steering capability for every backend", () => {
    expect(backendSupportsSteer("hermes")).toBe(true);
    expect(backendSupportsSteer("codex")).toBe(false);
    expect(backendSupportsSteer("claude")).toBe(false);
    expect(backendSupportsSteer(null)).toBe(false);
  });

  it("normalizes legacy text while preserving stored message pieces", () => {
    expect(messageContentOf({ text: "hello" }))
      .toEqual([{ piece: "text", text: "hello" }]);

    const pieces = [
      { piece: "text" as const, text: "look at this" },
      {
        piece: "image" as const,
        stored_file_id: "file-1",
        media_type: "image/png",
        file_name: "shot.png"
      }
    ];
    expect(messageContentOf({ content: pieces })).toBe(pieces);
    expect(messageContentText(pieces)).toBe("look at this");
  });

  it("encodes Conversation and file identities as path segments", () => {
    expect(conversationFileHref("conversation-1", "file-1"))
      .toBe("/api/conversation/conversations/conversation-1/files/file-1");
    expect(conversationFileHref("a/b", "file 1"))
      .toBe("/api/conversation/conversations/a%2Fb/files/file%201");
  });
});
