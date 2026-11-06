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
      { kind: "command_reply", reply_text: "Goal: ship the fix\nactive · 0 tokens" }
    );
    const html = render(ConversationTranscript, {
      props: {
        rows: [], visibleRows: [], lens: "focus", conversationId: "c",
        projectedItems: [{ kind: "row", key: row.key, row, behindTheFoldOf: null }]
      }
    }).body;
    expect(html).toContain('data-conversation-row="command_reply"');
    expect(html).toContain("Goal: ship the fix\nactive · 0 tokens");
  });
});
