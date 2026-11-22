import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import HeldPromptStack from "../src/components/conversation/composer/HeldPromptStack.svelte";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const v11 = app.slice(app.indexOf("v11 (22 Nov, after round 17)"));
const composer = source("../src/components/conversation/ConversationComposer.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");

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

describe("v11 (1): narration in Full is activity", () => {
  it("13/20 #a6a6a6, in Full only (v12 (2) removed the 24px indent)", () => {
    expect(v11).toContain('[data-conversation-pane][data-conversation-lens="full"] :is(.chat-a--narration, .c2-streaming) .markdown { font-size: 13px; line-height: 20px; color: #a6a6a6; }');
    expect(source("../src/components/conversation/ToolRunSummary.svelte")).toMatch(/\.c2-run-summary \{[^}]*column-gap: var\(--cv-s2\);/);
  });
  it("inline code in narration: the same 13px, #a6a6a6, no fill", () => {
    expect(v11).toContain(":is(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code { font-size: 13px; line-height: 20px; padding: 0; border-radius: 0; background: none; color: #a6a6a6; }");
  });
});

describe("v11 (2): option rows", () => {
  const ask = { askId: "a", title: "Upload build 41", detail: "xcrun altool --upload-app", options: [
    { option_id: "allow", label: "Allow once", option_kind: "allow_once" as const },
    { option_id: "deny", label: "Deny", option_kind: "reject_once" as const }] };
  it("the Deny row of a permission ask is marked as the declining row, its label #f08a7e", () => {
    const html = render(PermissionAskCard, { props: { ask, docked: true, onAnswer: () => {} } }).body;
    expect(html).toMatch(/data-conversation-ask-choice="deny"[^>]*data-conversation-ask-emphasis="decline"/);
    const card = source("../src/components/conversation/PermissionAskCard.svelte");
    expect(card).toContain('.c2-ask-choice[data-conversation-ask-emphasis="decline"] :global(.cv-ask-label) { color: #f08a7e; }');
  });
  it("v11 (8): the command block's edge is #3a3a3a", () => {
    expect(source("../src/components/conversation/PermissionAskCard.svelte")).toMatch(/\.c2-ask-detail \{[^}]*border: 1px solid #3a3a3a;/);
  });
});

describe("v11 (4): queued notes", () => {
  it("a status line (no pill): clock, Queued #ececec 500, ' · ' and 'n of m' #c4c4c4, then a 28px more button", () => {
    const html = render(HeldPromptStack, { props: { rows: [row(), row({ key: "h2", heldPromptId: "hp-2" })], onEdit: () => {} } }).body;
    expect(html).toContain("1 of 2");
    expect(html).toContain("2 of 2");
    expect(html.match(/data-conversation-held-more/g)?.length).toBe(2);
    expect(html).toMatch(/<span class="chat-qrow-note[^"]*" data-conversation-held-chip/);
    expect(held).toContain(".chat-qrow-word { color: #ececec; font-weight: var(--cv-weight-label); }");
    expect(held).toContain(".chat-qrow-detail { color: #c4c4c4;");
    expect(held).toMatch(/\.chat-qrow-more \{[^}]*width: 28px;\s*height: 28px;[^}]*background: none;/);
  });
  it("the menu offers Send now, Edit and Remove, in that order", () => {
    const html = render(HeldPromptStack, { props: { rows: [row()], onEdit: () => {} } }).body;
    const menu = html.slice(html.indexOf("data-conversation-held-menu"));
    const sendNow = menu.indexOf(">Send now<");
    const edit = menu.indexOf(">Edit<");
    const remove = menu.indexOf(">Remove<");
    expect(sendNow).toBeGreaterThan(-1);
    expect(edit).toBeGreaterThan(sendNow);
    expect(remove).toBeGreaterThan(edit);
    expect(menu).toContain('data-conversation-held-edit="hp-1"');
  });
  it("Edit is offered only for a words-only message the server holds (a picture cannot go back as it was sent)", () => {
    const picture = row({ content: [{ piece: "image", media_type: "image/png", stored_file_id: "f", file_name: "chart.png" } as never, { piece: "text", text: "Match this." }] });
    expect(render(HeldPromptStack, { props: { rows: [picture], onEdit: () => {} } }).body).not.toContain("data-conversation-held-edit");
    expect(render(HeldPromptStack, { props: { rows: [row()] } }).body).not.toContain("data-conversation-held-edit");
  });
  it("a message still sending shows Sending and no more button", () => {
    const html = render(HeldPromptStack, { props: { rows: [row({ heldPromptId: null, state: "in_flight" })] } }).body;
    expect(html).toContain("Sending");
    expect(html).not.toContain("data-conversation-held-more");
  });
  it("Edit takes the message out of the queue, then puts its words in the composer", () => {
    expect(pane).toMatch(/async function editHeldPrompt\(heldPromptId: string, words: string\): Promise<void> \{[\s\S]*?await onDiscardHeldPrompt\(heldPromptId\);[\s\S]*?if \(heldPromptRows\.some\(\(row\) => row\.heldPromptId === heldPromptId\)\) return;\s*composerHandle\.takeBackQueuedWords\(words\);/);
    expect(pane).toContain("bind:this={composerHandle}");
    expect(composer).toMatch(/export function takeBackQueuedWords\(words: string\): void \{\s*const settled = text\.trimEnd\(\);\s*text = settled === "" \? words : `\$\{settled\}\\n\\n\$\{words\}`;/);
  });
  it("choosing Edit fades the row, then calls onEdit with the held prompt and its words", () => {
    // The tests run without a DOM; the wiring is read from the source.
    expect(held).toContain("onclick={() => { menuOpenFor = null; if (row.heldPromptId !== null) void act(row.key, () => onEdit?.(row.heldPromptId!, words)); }}");
    expect(held).toMatch(/async function act\(key: string, action: \(\) => Promise<void> \| void\): Promise<void> \{[\s\S]*?await fadeConversationExit\(row \?\? null\);[\s\S]*?await action\(\);/);
  });
});

describe("v11 (3, 8, 12): the composer", () => {
  it("disabled send #333333 with a #bdbdbd arrow", () => {
    expect(app).toMatch(/\.chat-send \{[^}]*background: #333333;\s*color: #bdbdbd;/);
  });
  it("8px between each control on the right (model, Steer, mic, Send/Stop)", () => {
    expect(composer).toContain(".chat-box:not(.has-ask) > .chat-foot > :global(.chat-submit) { gap: var(--cv-s2); }");
    expect(composer).toContain(".chat-box:not(.has-ask) > .chat-foot > :global([data-conversation-model-picker]) { margin-inline-end: var(--cv-s1); }");
  });
});

describe("v11 (9, 10): answers", () => {
  it("table heads 14/20 500 #bdbdbd", () => {
    expect(v11).toContain("[data-conversation-pane] .chat-thread .markdown th { color: #bdbdbd; font-weight: 500; }");
  });
  it("links #ececec with a 50% underline", () => {
    expect(v11).toContain("color: #ececec; text-decoration-color: rgb(236 236 236 / 50%); }");
  });
});

describe("v11 (3): pass 17 fit and finish", () => {
  it("the one Stop is #f2f2f2 with a #141414 square", () => {
    expect(app).toContain(".chat-send.stop { background: #f2f2f2; color: #141414; }");
  });
});

describe("v11 (5), p17c review: pictures in bubbles", () => {
  it("sent and queued pictures share one cap: 320 wide, 200 tall, the bubble as wide as its widest content", () => {
    expect(app).toContain(":is(.chat-u, .chat-qrow) .file-preview-media { max-width: min(100%, 320px);");
    expect(app).toMatch(/:is\(\.chat-u, \.chat-qrow\) \.file-preview-media > :is\(\.file-preview-image, \.file-preview-video-poster\) \{\s*max-width: 100%;\s*max-height: 200px;/);
    expect(app).toContain(":is(.chat-u, .chat-qrow) .file-preview-grid { width: min(100%, 320px);");
    expect(source("../src/components/conversation/MessagePieces.svelte")).toContain(".c2-bubble--one-image > .c2-bubble-words { width: 0; min-width: 100%; }");
    expect(held).not.toContain("max-height: 96px");
  });
});
