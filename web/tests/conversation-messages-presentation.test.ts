import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";

import ToolRunSummary from "../src/components/conversation/ToolRunSummary.svelte";
import UserMessage from "../src/components/conversation/UserMessage.svelte";

import type { ToolCallPresentation, ToolRunSummary as Summary } from "../src/lib/conversation/toolCallPresentation";

// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { existsSync } from "node:fs";

import MessagePieces from "../src/components/conversation/MessagePieces.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
const pieces = source("../src/components/conversation/MessagePieces.svelte");
const user = source("../src/components/conversation/UserMessage.svelte");
const run = source("../src/components/conversation/ToolRunSummary.svelte");
const tool = source("../src/components/conversation/ToolCallRow.svelte");
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const pane = source("../src/components/conversation/ConversationPane.svelte");
const shell = source("../src/components/conversation/AskShell.svelte");
const status = source("../src/components/conversation/TranscriptStatusRow.svelte");

describe("messages", () => {
  it("a queued message's words are #d9d9d9 in the ghost frame", () => {
    const stack = source("../src/components/conversation/composer/HeldPromptStack.svelte");
    expect(stack).toMatch(/\.chat-qrow :global\(\.c2-bubble\) \{[^}]*rgb\(255 255 255 \/ 16%\);[^}]*color: var\(--cv-t2\);/);
  });
  it("an undelivered message has the small 16px ! beside its bubble, in the failure colour #f08a7e", () => {
    expect(source("../src/components/conversation/UserMessage.svelte")).toMatch(/\.chat-u\.undelivered :global\(\.c2-bubble\)::before \{[^}]*right: calc\(100% \+ 8px\);\s*width: 16px;\s*height: 16px;[^}]*background: #f08a7e;/);
  });
  it("a message from Panels uses a compact system note", () => {
    const row = {
      key: "e1", kind: "prompt" as const, sequence: 1, createdAt: 1, senderLabel: "Panels", senderMessageId: null,
      content: [{ piece: "text" as const, text: "Continue the Ticket at Stage: Problem understanding." }]
    };
    const html = render(UserMessage, { props: { row, conversationId: "c", ownSenderLabel: "owner" } }).body;
    expect(html).toMatch(/<p class="c2-system-note[^"]*" data-conversation-system-note[^>]*>(<!--[^>]*-->)*<span class="c2-system-words[^"]*"><span class="c2-label[^"]*" data-conversation-prompt-label[^>]*>From Panels<\/span><span aria-hidden="true"> · <\/span>Continue the Ticket at Stage: Problem understanding\.<\/span><\/p>/);
    expect(html).not.toContain("c2-system-row");
    const css = source("../src/components/conversation/UserMessage.svelte");
    expect(css).toMatch(/\.c2-system-note \{[^}]*padding: 8px 12px;\s*border-radius: 10px;\s*background: #1a1a1a;[^}]*font: var\(--cv-weight-body\) 13px\/20px var\(--cv-font\);/);
    expect(css).toMatch(/\.c2-system-words \{[^}]*-webkit-line-clamp: 2;/);
    expect(css).toContain(".c2-label { color: #bdbdbd; font-weight: var(--cv-weight-label); }");
    expect(html).not.toContain("<img");
    expect(css).not.toContain("c2-system-mark");
  });
  it("another sender's note wears the same well", () => {
    const row = {
      key: "e2", kind: "prompt" as const, sequence: 1, createdAt: 1, senderLabel: "Chief of Staff", senderMessageId: null,
      content: [{ piece: "text" as const, text: "Look at the flaky test first." }]
    };
    const html = render(UserMessage, { props: { row, conversationId: "c", ownSenderLabel: "owner" } }).body;
    expect(html).toContain("From Chief of Staff");
    expect(html).not.toContain("data-conversation-system-mark");
  });
});

describe("Full view tool rows", () => {
  const closed: Summary = { sentence: "Ran 2 commands", failedCount: 0, running: null, iconPaths: [] };
  it("running: \"Running\", the command in mono, then the chevron at the row's end", () => {
    const running = { title: "Running", summary: "go test -race ./internal/push/...", summaryIsLiteral: true, sentence: "Running go test" } as unknown as ToolCallPresentation;
    const html = render(ToolRunSummary, { props: { summary: { ...closed, running }, callCount: 3, onToggle: () => { } } }).body;
    const verb = html.indexOf(">Running<");
    const command = html.indexOf("go test -race");
    const chevron = html.indexOf("c2-run-chevron");
    expect(verb).toBeGreaterThan(-1);
    expect(verb).toBeLessThan(command);
    expect(command).toBeLessThan(chevron);
    expect(html).toMatch(/class="c2-run-subject[^"]*c2-run-subject--literal/);
  });
});

describe("inline code spacing in the markdown rendering", () => {

  it("marks a code span followed by closing punctuation, and one that opens a list item", async () => {
    const { inlineCodeSpacing } = await import("../src/lib/markdownPipeline");
    // "at `batcher.go:97`, read" — the comma sits against the chip.
    expect(inlineCodeSpacing("P", ["at ", null, ", read"], 1)).toEqual(["cv-code-before-punctuation"]);
    expect(inlineCodeSpacing("P", ["at ", null, "."], 1)).toEqual(["cv-code-before-punctuation"]);
    expect(inlineCodeSpacing("P", ["at ", null, " while"], 1)).toEqual([]);
    // "- `flush()` swaps" — the chip opens the item, so its pad hangs outside the text edge.
    expect(inlineCodeSpacing("LI", [null, " swaps"], 0)).toEqual(["cv-code-leads"]);
    expect(inlineCodeSpacing("LI", ["\n", null, ")"], 1)).toEqual(["cv-code-before-punctuation", "cv-code-leads"]);
    expect(inlineCodeSpacing("LI", ["read ", null], 1)).toEqual([]);
    expect(inlineCodeSpacing("P", [null, " swaps"], 0)).toEqual([]);
  });
});

describe("one agent face (Inter), no serif", () => {
  it("Newsreader and its notice are gone; nothing in the app names a serif face", () => {
    expect(existsSync(new URL("../../assets/fonts/Newsreader.woff2", import.meta.url))).toBe(false);
    expect(existsSync(new URL("../../assets/fonts/Newsreader-Italic.woff2", import.meta.url))).toBe(false);
    expect(existsSync(new URL("../../assets/fonts/Newsreader-OFL.txt", import.meta.url))).toBe(false);
    expect(tokens).not.toContain("Newsreader");
    expect(app).not.toContain("--cv-font-answer");
    expect(source("../../THIRD_PARTY_NOTICES.md")).not.toContain("Newsreader");
    expect(`${app}\n${tokens}`.replace(/sans-serif/g, "")).not.toMatch(/(?:font-family|font|--[\w-]*font[\w-]*)\s*:[^;}]*\bserif\b/);
  });
});

describe("shared presentation", () => {
  it("1 Surface: composer, newest error, attachment cards, code blocks; bubbles #242424 radius 18 padding 12 16", () => {
    expect(app).toMatch(/\.chat-box \{[^}]*background: #1c1c1c;\s*border: 1px solid #2a2a2a;\s*border-radius: 20px;\s*box-shadow: none;/);
    expect(source("../src/components/conversation/ConversationFileCard.svelte")).toMatch(/border: 1px solid var\(--cv-surface-edge, #2a2a2a\);\s*border-radius: var\(--cv-surface-radius, 20px\);\s*background: var\(--cv-surface, #1c1c1c\);/);
    expect(source("../src/lib/markdown/codeBlocks.css")).toMatch(/border-radius: 12px;\s*background: var\(--cv-code-surface, #1a1a1a\);/);
    expect(source("../src/components/conversation/MessagePieces.svelte")).toMatch(/background: #242424;\s*color: #ececec;\s*border-radius: 18px;\s*padding: 12px 16px;/);
  });
});

describe("messages and asks", () => {
  it("attachments sit inside the user bubble, above its words; 8 around them, 12 16 around the words", () => {
    const html = render(MessagePieces, {
      props: {
        bubble: true, conversationId: "c", content: [
          { piece: "text", text: "Use this export." },
          { piece: "file", stored_file_id: "f1", file_name: "export.csv", media_type: "text/csv", byte_count: 1700 }
        ]
      }
    }).body;
    expect(html.match(/data-conversation-bubble[ =>]/g)?.length).toBe(1);
    expect(html.indexOf("conversation-file-card")).toBeLessThan(html.indexOf("c2-bubble-words"));
    const words = render(MessagePieces, { props: { bubble: true, conversationId: "c", content: [{ piece: "text", text: "Hi" }] } }).body;
    expect(words).not.toContain("c2-bubble--attached");
    const css = source("../src/components/conversation/MessagePieces.svelte");
    expect(css).toContain(".c2-bubble--attached { display: flex; flex-direction: column; gap: 8px; padding: 8px; }");
    expect(css).toContain(".c2-bubble-words { padding: 4px 8px; min-width: 0; }");
  });
});

describe("thread pieces", () => {
  it("file cards fill the bubble; a one-image bubble is as wide as its image", () => {
    const one = render(MessagePieces, {
      props: {
        bubble: true, conversationId: "c", content: [
          { piece: "image", stored_file_id: "i1", file_name: "chart.png", media_type: "image/png", byte_count: 10 },
          { piece: "text", text: "Match this." }]
      }
    }).body;
    expect(one).toContain("c2-bubble--one-image");
    const file = render(MessagePieces, {
      props: {
        bubble: true, conversationId: "c", content: [
          { piece: "file", stored_file_id: "f1", file_name: "export.csv", media_type: "text/csv", byte_count: 1700 },
          { piece: "text", text: "Use this." }]
      }
    }).body;
    expect(file).not.toContain("c2-bubble--one-image");
    const css = source("../src/components/conversation/MessagePieces.svelte");
    expect(css).toContain(".c2-bubble-attachments > :global(.conversation-file-card) { align-self: stretch; width: auto; max-width: none; }");
    expect(css).toContain(".c2-bubble--one-image { min-width: 240px; }");
    expect(css).toContain(".c2-bubble--one-image > .c2-bubble-words { width: 0; min-width: 100%; }");
  });
});

describe("pictures in bubbles", () => {
  it("sent and queued pictures share one cap: 320 wide, 200 tall, the bubble as wide as its widest content", () => {
    expect(app).toContain(":is(.chat-u, .chat-qrow) .file-preview-media { max-width: min(100%, 320px);");
    expect(app).toMatch(/:is\(\.chat-u, \.chat-qrow\) \.file-preview-media > :is\(\.file-preview-image, \.file-preview-video-poster\) \{\s*max-width: 100%;\s*max-height: 200px;/);
    expect(app).toContain(":is(.chat-u, .chat-qrow) .file-preview-grid { width: min(100%, 320px);");
    expect(source("../src/components/conversation/MessagePieces.svelte")).toContain(".c2-bubble--one-image > .c2-bubble-words { width: 0; min-width: 100%; }");
    expect(held).not.toContain("max-height: 96px");
  });
});

describe("agent answer presentation", () => {
  it("no answer bubble: no rule gives a final answer a surface, radius or padding", () => {
    expect(app).not.toMatch(/\.chat-thread \.chat-a:not\(\.chat-a--narration, \.c2-streaming\) \{/);
    expect(app).not.toContain(":has(.chat-chart, .goal-chart-host) { width: 100%; }");
    expect(app).not.toContain(".chat-a:not(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code { background: #2a2a2a; }");
  });
});

describe("thread pieces", () => {
  it("a one-picture bubble is as wide as its picture", () => {
    expect(pieces).toContain(".c2-bubble--one-image :global(.file-preview-media > :is(.file-preview-image, .file-preview-video-poster)) { max-width: 320px; }");
  });
  it("the first queued bubble stands 24px under the Working line", () => {
    expect(held).toContain(".chat-queue-stack { padding-block-start: 24px; }");
    expect(held).toContain(".chat-queue-stack { row-gap: 24px; }");
  });
  it("the From Panels note is centred", () => {
    expect(user).toContain(".c2-system-note { align-self: center; }");
  });
});

describe("secondary text contrast", () => {
  it("option descriptions, attachment meta, the empty hint, From Panels, queued and steered details", () => {
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-sub) { color: #c4c4c4; }");
    expect(composer).toContain(".chat-att-meta { color: #b8b8b8;");
    expect(pane).toMatch(/\.cv-empty-hint \{[^}]*color: #b8b8b8;/);
    expect(user).toContain(".c2-system-note { color: #c4c4c4; }");
    expect(held).toContain(".chat-qrow-note, .chat-qrow-sep, .chat-qrow-detail { color: #c4c4c4; }");
    expect(status).toContain(".c2-status[data-conversation-steered] :is(.c2-status-next, .c2-status-sep) { color: #c4c4c4; }");
  });
});
