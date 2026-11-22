import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";
import { fitTitle, TITLE_ELLIPSIS } from "../src/lib/conversation/titleFit";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const shell = source("../src/components/conversation/AskShell.svelte");
const questions = source("../src/components/conversation/UserInputQuestionPanel.svelte");
const pieces = source("../src/components/conversation/MessagePieces.svelte");
const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
const user = source("../src/components/conversation/UserMessage.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const live = source("../src/components/conversation/LiveConversation.svelte");

describe("v13 (1): one inline-code style", () => {
  it("#232323, radius 4, padding 1px 4px everywhere in the conversation", () => {
    expect(app).toContain("[data-conversation-pane] .cv-code { padding: 1px 4px; border-radius: 4px; background: #232323; }");
  });
  it("in narration: the same chip, #b4b4b4 text at 13px; narration text #b0b0b0 13/20", () => {
    expect(app).toContain('[data-conversation-pane][data-conversation-lens="full"] .chat-thread :is(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code { font-size: 13px; line-height: 1; padding: 1px 4px; border-radius: 4px; background: #232323; color: #b4b4b4; }');
    expect(app).toContain('[data-conversation-pane][data-conversation-lens="full"] :is(.chat-a--narration, .c2-streaming) .markdown { color: #b0b0b0; }');
  });
});

// v13 (2), (3) and (4) were replaced by v14 (2), (4) and (3): see conversation-design-v14.test.ts.

describe("v13 (5): the question pager", () => {
  const request = {
    requestId: "r1",
    questions: [
      { question_id: "a", header: "A", question: "First?", multi_select: false, allow_other: false, options: [{ label: "One", description: "" }, { label: "Two", description: "" }] },
      { question_id: "b", header: "B", question: "Second?", multi_select: false, allow_other: false, options: [{ label: "Three", description: "" }] },
    ],
  };
  it("an unavailable arrow takes no pointer (v14 (4): #6e6e6e)", () => {
    expect(shell).toContain(":global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button:disabled) { color: #6e6e6e; background: none; cursor: default; pointer-events: none; }");
  });
  it("on 1 of 2 back is unavailable and forward is not; forward is unavailable only on the last", () => {
    const body = render(UserInputQuestionPanel, { props: { request, onSubmit: () => {} } as never }).body;
    expect(body).toMatch(/data-user-input-back[^>]*disabled/);
    expect(body).not.toMatch(/data-user-input-forward[^>]*disabled/);
    expect(questions).toContain('disabled={busy || isLast}\n      onclick={goForward}');
  });
  it("a passed-over question is returned to when the last choice cannot send yet", () => {
    expect(questions).toContain("if (open >= 0) step = open;");
  });
});

describe("v13 (6): composer attachment chips", () => {
  it("picture and file: one height, radius 10, the remove circle 6px inside the top-right corner", () => {
    expect(composer).toMatch(/\.chat-att \{\s*height: 76px;[^}]*border-radius: 10px;/);
    expect(composer).toMatch(/\.chat-att-x \{\s*top: 6px;\s*right: 6px;\s*width: 20px;\s*height: 20px;/);
    expect(composer).toContain(".chat-att.file > .chat-att-x { top: 5px; right: 5px; }");
  });
  it("a file chip ends just after the circle; a picture crops from the top", () => {
    expect(composer).toContain("padding-inline: 12px 40px;");
    expect(composer).toContain(".chat-att.img img { border-radius: 10px; object-position: left top; }");
  });
});

describe("v13 (7, 8, 10): thread pieces", () => {
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

describe("v13 (9): table head", () => {
  it("#ececec 600 14/20 over a #3a3a3a rule", () => {
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown th { color: #ececec; font-size: 14px; line-height: 20px; font-weight: 600; border-block-end: 1px solid #3a3a3a; }");
  });
});

describe("v13 (11): a truncated title has no space before the ellipsis", () => {
  const fitsIn = (width: number) => (candidate: string): boolean => candidate.length <= width;
  it("keeps a title that fits", () => {
    expect(fitTitle("Wren public beta", fitsIn(40))).toBe("Wren public beta");
  });
  it("trims the space a cut after a word leaves", () => {
    const shown = fitTitle("Fix the flaky TestPushMergesConcurrentEdits on CI", fitsIn(45));
    expect(shown).toBe(`Fix the flaky TestPushMergesConcurrentEdits${TITLE_ELLIPSIS}`);
    expect(shown).not.toMatch(/\s…$/);
  });
  it("is the longest prefix that fits", () => {
    expect(fitTitle("abcdefghij", fitsIn(6))).toBe(`abcde${TITLE_ELLIPSIS}`);
    expect(fitTitle("abcdefghij", fitsIn(0))).toBe(TITLE_ELLIPSIS);
  });
  it("the header title uses it", () => {
    expect(pane).toContain("use:fittedTitle={headTitle}");
  });
});

describe("v13 (12): empty state", () => {
  it("the hint stands at the foot of the canvas, just above the composer", () => {
    expect(live).toContain(".conversation-empty { display: flex; align-items: flex-end; justify-content: center; flex: 1 0 auto; align-self: stretch; }");
  });
});
