import { afterEach, describe, expect, it, vi } from "vitest";

import {
  COMMITTED_EVENT_STREAM_NAME,
  CONVERSATION_BACKEND_KEYS,
  CONVERSATION_BASE,
  LIVE_FRAME_STREAM_NAME,
  backendSupportsSteer,
  conversationFileHref,
  messageContentOf,
  messageContentText,
  refreshBackendUsage
} from "../src/lib/conversation/wire";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

  it("acquires provider usage only through the explicit backend action", async () => {
    const fetch = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({
        backend_key: "codex",
        outcome: "succeeded",
        detail: null,
        observed_at: "2026-07-31T12:34:56Z",
        windows: [{ name: "5 hours", used_percent: 12.5, resets_at: "2026-07-31T15:00:00Z" }]
      }), { status: 200 })
    );
    vi.stubGlobal("fetch", fetch);

    const answer = await refreshBackendUsage("codex");

    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledWith(
      "/api/conversation/backends/codex/usage-refresh",
      { method: "POST" }
    );
    expect(answer.windows[0]).toMatchObject({ name: "5 hours", used_percent: 12.5 });
  });
});
