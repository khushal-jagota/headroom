import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PaneWithQueryClient from "./fixtures/PaneWithQueryClient.svelte";
import { ASK_REPLY_PLACEHOLDER, COMPOSER_PLACEHOLDER, composerPlaceholderFor } from "../src/lib/conversation/runControls/logic/runSelection";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const pane = source("../src/components/conversation/ConversationPane.svelte");
const live = source("../src/components/conversation/LiveConversation.svelte");
const shell = source("../src/components/conversation/AskShell.svelte");
const anchor = source("../src/components/conversation/TurnAnchor.svelte");
const status = source("../src/components/conversation/TranscriptStatusRow.svelte");
const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");

describe("v15 (1): the grown composer starts at its own inner padding", () => {
  it("drops v14 (10)'s 41px indent of the draft and the chips", () => {
    expect(composer).not.toContain("padding-inline-start: 41px");
  });
});

describe("v15 (2): the attachment remove button", () => {
  it("is a dark 20px circle with a light ring and an #ececec x, 6px inside the corner of both chips", () => {
    expect(composer).toMatch(/\.chat-att-x \{[^}]*width: 20px;[^}]*background: rgb\(20 20 20 \/ 85%\);[^}]*box-shadow: inset 0 0 0 1px rgb\(255 255 255 \/ 24%\);[^}]*color: #ececec;/);
    // The file chip's 1px edge is part of its box, so its circle is 5px inside the edge.
    expect(composer).toContain(".chat-att.file > .chat-att-x { top: 5px; right: 5px; }");
    expect(composer).not.toContain("top: -10px; right: -10px;");
  });
  it("the file chip has 12px before its icon and room for the button only after its words", () => {
    expect(composer).toContain("padding-inline: 12px 40px;");
    expect(composer).toContain(".chat-attachments { gap: var(--cv-s2); padding-block-start: 0; margin-block-start: 0; padding-inline-end: 0; }");
  });
});

describe("v15 (3): narration in Full", () => {
  it("is 14/24 #c4c4c4, its inline code unfilled monospace at 0.9em in #dcdcdc", () => {
    expect(app).toContain('[data-conversation-pane][data-conversation-lens="full"] :is(.chat-a--narration, .c2-streaming) .markdown { font-size: 14px; line-height: 24px; color: #c4c4c4; }');
    expect(app).toContain("font-family: var(--cv-mono); font-size: 0.9em; line-height: inherit; padding: 0; border-radius: 0; background: none; color: #dcdcdc; }");
  });
});

describe("v15 (4): the Focus/Full control", () => {
  it("the shown lens is #363636 with #f2f2f2 500; the other is #a6a6a6 400 with no fill", () => {
    expect(pane).toContain(".cv-segment { color: #a6a6a6; font-weight: 400; }");
    expect(pane).toContain('.cv-segment[aria-pressed="true"], .cv-segment[aria-pressed="true"]:hover { background: #363636; color: #f2f2f2; font-weight: 500; }');
  });
});

describe("v15 (5): quiet text contrast", () => {
  it("Worked for #c4c4c4, the Working timer #b8b8b8", () => {
    expect(anchor).toContain(":is(.c2-meta--fold, [data-conversation-turn-settled-head]), .c2-meta--fold .c2-meta-sep { color: #c4c4c4; }");
    expect(anchor).toContain(".c2-meta-clock { color: #b8b8b8; }");
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain(".c2-rest-clock { color:#b8b8b8; }");
  });
  it("Steered and Queued details #b8b8b8, ask option descriptions #b8b8b8, disabled pager arrow #7a7a7a", () => {
    expect(status).toContain(".c2-status[data-conversation-steered] :is(.c2-status-next, .c2-status-sep) { color: #b8b8b8; }");
    expect(held).toContain(".chat-qrow-note, .chat-qrow-sep, .chat-qrow-detail { color: #b8b8b8; }");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-sub) { color: #b8b8b8; }");
    expect(shell).toContain(":global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button:disabled) { color: #7a7a7a; }");
  });
  it("placeholders #9e9e9e inside the conversation; the queued more button #bdbdbd", () => {
    expect(tokens).toContain("--cv-placeholder: #9e9e9e; /* v15 (5) */");
    expect(held).toMatch(/\.chat-qrow-more \{[^}]*color: #bdbdbd;/);
  });
});

describe("v15 (6): the empty conversation centres its composer", () => {
  const props = { conversationId: "c-empty", draftKey: "v15-empty", onSend: async () => true };
  it("an opened, empty conversation puts the hint under the composer and marks the pane", () => {
    const { body } = render(PaneWithQueryClient, { props: { ...props, conversationState: "opened", emptyHint: "You can attach or drop files here." } });
    expect(body).toContain('data-conversation-empty-start="true"');
    const hint = body.indexOf("data-conversation-empty-hint");
    expect(hint).toBeGreaterThan(body.indexOf("data-conversation-input"));
    expect(body).toContain("You can attach or drop files here.</p>");
  });
  it("the docked conversation at rest keeps its composer at the foot (no hint, no mark)", () => {
    const { body } = render(PaneWithQueryClient, { props: { ...props, conversationState: "rest", emptyHint: "You can attach or drop files here." } });
    expect(body).not.toContain("data-conversation-empty-start");
    expect(body).not.toContain("data-conversation-empty-hint");
  });
  it("a conversation without the hint (one with turns) is unchanged", () => {
    const { body } = render(PaneWithQueryClient, { props: { ...props, conversationState: "opened" } });
    expect(body).not.toContain("data-conversation-empty-start");
  });
  it("the heading is 20/28 600 #ececec; the hint 13/20 #a6a6a6, 12px under the composer", () => {
    expect(live).toContain('<h2 class="conversation-empty-said">What should we work on?</h2>');
    expect(live).toContain(".conversation-empty-said { margin: 0 0 -4px; color: #ececec; font: 600 20px/28px var(--cv-font);");
    expect(live).toContain('emptyHint={!emptyState && !readOnly && nothingSaidYet ? "You can attach or drop files here." : null}');
    // v16 (12): the hint is #b8b8b8.
    expect(pane).toMatch(/\.cv-empty-hint \{[^}]*margin: 12px 0 0;[^}]*color: #b8b8b8;[^}]*13px\/20px/);
    expect(pane).toContain(".cv-empty-foot { flex: 1 1 0; min-height: 0; }");
    expect(pane).toContain(".chat-panel.is-empty-start > .conversation-reader { flex: 1 1 0; }");
  });
});

describe("v15 (7): asks", () => {
  it("every ask says Or write a reply; no ask keeps the base placeholder", () => {
    expect(ASK_REPLY_PLACEHOLDER).toBe("Or write a reply");
    for (const askWaiting of ["answers", "holds"] as const) {
      for (const mode of ["queue", "send_now", "steer"] as const) {
        for (const running of [true, false]) {
          expect(composerPlaceholderFor({ askWaiting, mode, running, base: "x" })).toBe("Or write a reply");
        }
      }
    }
    expect(composerPlaceholderFor({ askWaiting: null, mode: "queue", running: true, base: "x" })).toBe(COMPOSER_PLACEHOLDER);
  });
});

describe("v15 (7): ask spacing", () => {
  it("16px between the last option and the answer well", () => {
    expect(composer).toMatch(/\.chat-box > \.chat-ask \{[^}]*margin-block-end: var\(--cv-s4\);/);
  });
});

describe("v15 (8): answer headings", () => {
  it("h1, h2 and h3 are one style: 17/24 600 #ececec, 24px above and 8px below", () => {
    expect(app).toContain("[data-conversation-pane] .chat-thread .chat-a .markdown :is(h1, h2, h3) + * { margin-block-start: 0; }");
    expect(app).toContain("[data-conversation-pane] .chat-thread .chat-a .markdown :is(h1, h2, h3) { margin: 24px 0 8px; font-size: 17px; line-height: 24px; font-weight: 600; letter-spacing: 0; color: #ececec; }");
  });
});
