import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import HeldPromptStack from "../src/components/conversation/composer/HeldPromptStack.svelte";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";

function row(overrides: Partial<HeldPromptRow> = {}): HeldPromptRow {
  return {
    key: "h1",
    heldPromptId: "hp-1",
    senderMessageId: "m-1",
    content: [{ piece: "text", text: "Log each 429 with the token prefix." }],
    senderLabel: "owner",
    sentAtUnixMilliseconds: 1,
    state: "held",
    queueReason: null,
    ...overrides
  } as HeldPromptRow;
}

const held = (key: string, text: string): HeldPromptRow =>
  row({ key, heldPromptId: `hp-${key}`, senderMessageId: null, content: [{ piece: "text", text }] });

describe("the queue of waiting messages", () => {
  it("draws each waiting message as the person's own message not sent yet, with its actions and no header", () => {
    const { body } = render(HeldPromptStack, {
      props: { rows: [held("a", "Log each 429"), held("b", "Match the dark style")], supportsSteer: true, running: true, conversationId: "c-1" }
    });
    expect(body).not.toContain("chat-queue-head");
    expect(body).toContain('aria-label="2 queued messages"');
    expect(body.match(/data-conversation-held-row=/g)?.length).toBe(2);
    // Each is a ghost bubble of the person's words, with one "Queued" chip under it that
    // opens its actions as a dropdown, drawn closed.
    expect(body.match(/data-conversation-bubble/g)?.length).toBe(2);
    expect(body.match(/cv-system-line-words[^"]*">(<!--[^>]*-->)*Queued/g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-chip/g)?.length).toBe(2);
    expect(body.match(/aria-haspopup="menu"/g)?.length).toBe(2);
    expect(body.match(/class="chat-qrow-menu[^"]*"[^>]*hidden/g)?.length).toBe(2);
    expect(body).not.toContain("sends when this turn ends");
    expect(body.indexOf("Log each 429")).toBeLessThan(body.indexOf("Match the dark style"));
    expect(body.match(/data-conversation-held-promote="steer"[^>]*>Steer now</g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-promote="send_now"/g)?.length).toBe(2);
    expect(body.match(/data-conversation-held-discard="hp-/g)?.length).toBe(2);
    expect(body).toContain('aria-label="Remove this message from the queue"');
  });

  it("says 'Queued · n of m' in one system line, then a more button, and draws no chevron", () => {
    const html = render(HeldPromptStack, { props: { rows: [row(), row({ key: "h2", heldPromptId: "hp-2" })], onEdit: () => { } } }).body;
    expect(html).toContain("1 of 2");
    expect(html).toContain("2 of 2");
    expect(html.match(/data-conversation-held-more/g)?.length).toBe(2);
    expect(html).toMatch(/data-conversation-system-line="end"[^>]*data-conversation-held-chip/);
    expect(html).toMatch(/Queued(<!--[^>]*-->)*<span aria-hidden="true"> · <\/span><span data-conversation-held-detail="">1 of 2</);
    expect(html).not.toContain("chevron");
  });

  it("offers no Steer for a turn that cannot take one", () => {
    const { body } = render(HeldPromptStack, { props: { rows: [held("a", "x")], supportsSteer: false, running: true } });
    expect(body).not.toContain('data-conversation-held-promote="steer"');
    expect(body).toContain('data-conversation-held-promote="send_now"');
  });

  it("shows Sending and no more button for a message still sending", () => {
    const html = render(HeldPromptStack, { props: { rows: [row({ heldPromptId: null, state: "in_flight" })] } }).body;
    expect(html).toContain("Sending");
    expect(html).not.toContain("data-conversation-held-more");
  });
});

describe("a waiting message's menu", () => {
  it("offers Send now, Edit and Remove, in that order", () => {
    const html = render(HeldPromptStack, { props: { rows: [row()], onEdit: () => { } } }).body;
    const menu = html.slice(html.indexOf("data-conversation-held-menu"));
    const sendNow = menu.indexOf(">Send now<");
    const edit = menu.indexOf(">Edit<");
    const remove = menu.indexOf(">Remove<");
    expect(sendNow).toBeGreaterThan(-1);
    expect(edit).toBeGreaterThan(sendNow);
    expect(remove).toBeGreaterThan(edit);
    expect(menu).toContain('data-conversation-held-edit="hp-1"');
  });

  it("offers Edit only for a words-only message the server holds (a picture cannot go back as it was sent)", () => {
    const picture = row({ content: [{ piece: "image", media_type: "image/png", stored_file_id: "f", file_name: "chart.png" } as never, { piece: "text", text: "Match this." }] });
    expect(render(HeldPromptStack, { props: { rows: [picture], onEdit: () => { } } }).body).not.toContain("data-conversation-held-edit");
    expect(render(HeldPromptStack, { props: { rows: [row()] } }).body).not.toContain("data-conversation-held-edit");
  });

  it("fades the row on Edit, then calls onEdit with the held prompt and its words", () => {
    // The tests run without a DOM; the wiring is read from the source.
    const stack = readFileSync(new URL("../src/components/conversation/composer/HeldPromptStack.svelte", import.meta.url), "utf8") as string;
    expect(stack).toContain("onclick={() => { menuOpenFor = null; if (row.heldPromptId !== null) void act(row.key, () => onEdit?.(row.heldPromptId!, words)); }}");
    expect(stack).toMatch(/async function act\(key: string, action: \(\) => Promise<unknown> \| void\): Promise<void> \{[\s\S]*?await fadeConversationExit\(row \?\? null\);[\s\S]*?await action\(\);/);
  });
});
