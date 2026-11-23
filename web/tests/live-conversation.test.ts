import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import LiveConversationWithQueryClient from "./fixtures/LiveConversationWithQueryClient.svelte";

describe("the empty conversation", () => {
  it("says 'No messages yet' as one centred system line, with the composer in its usual place", () => {
    const body = render(LiveConversationWithQueryClient, {
      props: { persistenceKey: "live-conversation", label: "Chief", sendMessage: async () => ({}) as never }
    }).body;
    const empty = body.slice(body.indexOf("data-conversation-empty"));
    expect(empty).toMatch(/data-conversation-system-line="center"[\s\S]*?No messages yet/);
    expect(body).not.toContain("What should we work on?");
    expect(body).not.toContain("You can attach or drop files here.");
    expect(body.indexOf("data-conversation-empty")).toBeLessThan(body.indexOf("data-conversation-box"));
  });
});
