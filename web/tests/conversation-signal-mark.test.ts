import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import ConversationSignalMark from "../src/components/ConversationSignalMark.svelte";

describe("exact conversation signal marks", () => {
  it.each([
    { working: false, message_for_owner: false },
    { working: true, message_for_owner: false },
    { working: false, message_for_owner: true },
    { working: true, message_for_owner: true }
  ])("renders the independent facts %j", (signal) => {
    const html = render(ConversationSignalMark, {
      props: { signal, conversationId: "conversation-exact" }
    }).body;
    expect(html.includes('data-conversation-signal="working"')).toBe(signal.working);
    expect(html.includes('data-conversation-signal="message"')).toBe(signal.message_for_owner);
    expect(html.includes('stage-mark--current-running')).toBe(signal.working);
    expect(html.includes('stage-mark--current-awaiting-approval')).toBe(signal.message_for_owner);
    if (signal.working || signal.message_for_owner) {
      expect(html).toContain('data-conversation-id="conversation-exact"');
    }
  });

  it("keeps an absent conversation quiet", () => {
    const html = render(ConversationSignalMark, { props: { signal: null } }).body;
    expect(html).not.toContain("data-conversation-signal");
  });
});
