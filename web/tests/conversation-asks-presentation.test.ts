import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";

import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const shell = source("../src/components/conversation/AskShell.svelte");
const questions = source("../src/components/conversation/UserInputQuestionPanel.svelte");
const permission = source("../src/components/conversation/PermissionAskCard.svelte");
const tokens = source("../../assets/tokens.css");

describe("asks", () => {
  it("the own-answer line sits in a #141414 well, radius 12, 40 high; the round send is its last piece, inside it", () => {
    expect(composer).toMatch(/\.chat-box\.has-ask\.is-compact > \.chat-foot > \.chat-ask-pen \{[^}]*min-height: 40px;[^}]*border-radius: 20px 0 0 20px;[^}]*background: #141414;/);
    expect(composer).toMatch(/\.chat-box\.has-ask\.is-compact > \.chat-ta \{[^}]*padding-block: 8px;[^}]*background: #141414;/);
    // The microphone sits in the middle of the well; the send closes it.
    expect(composer).toMatch(/\.chat-submit > \.chat-voice-mic\) \{[^}]*border-radius: 0;[^}]*background: #141414;/);
    expect(composer).toMatch(/:global\(\.chat-submit > \.chat-ask-send:hover:not\(:disabled\)\) \{[^}]*width: 40px;[^}]*border-radius: 0 20px 20px 0;\s*background: #141414;\s*box-shadow: inset -1px 0 0 #333, inset 0 1px 0 #333, inset 0 -1px 0 #333;/);
    expect(composer).toContain(".chat-box.has-ask.is-compact > .chat-foot :global(.chat-submit > .chat-ask-send) { order: 3; }");

    expect(composer).not.toContain("chat-ask-stop");
    expect(composer).toMatch(/:global\(\.chat-submit > \.chat-ask-send:hover:not\(:disabled\)\) \{[^}]*margin: 0 var\(--cv-s2\) 0 0;/);
  });
});

describe("shared presentation", () => {
  it("Option rows: min 48, radius 12; #2a2a2a with a #363636 edge, keycap 24 #3a3a3a radius 6; hover/focus #303030, accent edge and keycap", () => {
    const shell = source("../src/components/conversation/AskShell.svelte");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-row) { min-height: 48px; padding: 12px 16px; border-radius: 12px; ");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-row) { background: #2a2a2a; box-shadow: inset 0 0 0 1px #363636; }");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-key) { border-radius: 6px; background: #3a3a3a;");

    // accent edge and an accent keycap. Nothing is lit otherwise.
    expect(shell).toContain(":global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible)) { background: #303030; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%); }");
    expect(shell).toContain(":global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible) .cv-ask-key) { background: var(--cv-accent); color: #141414; }");
    expect(shell).not.toContain("is-focused");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-rows) { gap: 8px; }");
  });
});

describe("asks", () => {
  it("a single-answer question has no forward pill; choosing a row moves on (the last choice sends)", () => {
    const panel = source("../src/components/conversation/UserInputQuestionPanel.svelte");
    expect(panel).toMatch(/if \(question\.multi_select\) \{\s*draft = toggleUserInputChoice\(draft, question, option\);\s*return;\s*\}[\s\S]*?continueOrSubmit\(\);\s*\}/);
    expect(panel).not.toContain("data-user-input-continue");
    expect(panel).not.toContain("onMount");
    const html = render(UserInputQuestionPanel, {
      props: {
        ownAnswer: "", request: {
          requestId: "q", questions: [
            { question_id: "a", header: "A", question: "Which style?", multi_select: false, allow_other: true, options: [{ label: "Soft", description: "" }, { label: "Line", description: "" }] }]
        }
      }
    }).body;
    expect(html).not.toContain("is-focused");
    expect(html).not.toContain("is-selected");
    expect(html).not.toContain("cv-button--primary");
  });
});

describe("option rows", () => {
  it("the command block's edge is #3a3a3a", () => {
    expect(source("../src/components/conversation/PermissionAskCard.svelte")).toMatch(/\.c2-ask-detail \{[^}]*border: 1px solid #3a3a3a;/);
  });
});

describe("the question pager", () => {
  it("an unavailable arrow takes no pointer : #6e6e6e)", () => {
    expect(shell).toContain(":global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button:disabled) { color: #6e6e6e; background: none; cursor: default; pointer-events: none; }");
  });
  it("a passed-over question is returned to when the last choice cannot send yet", () => {
    expect(questions).toContain("if (open >= 0) step = open;");
  });
});

describe("no ask option is lit at rest", () => {
  it("the first-row rest style is gone; keycaps are a neutral #3a3a3a; hover and focus keep the accent", () => {
    expect(shell).not.toContain(".cv-ask-row:first-child");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-key) { border-radius: 6px; background: #3a3a3a;");
    expect(shell).toContain(":global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible) .cv-ask-key) { background: var(--cv-accent); color: #141414; }");
  });
});

describe("disabled send and pager arrow", () => {
  it("an unavailable pager arrow is #6e6e6e", () => {
    expect(shell).toContain(".cv-ask-context :global(.cv-icon-button:disabled) { color: #6e6e6e;");
  });
});

describe("the command block in asks", () => {
  const ask = {
    askId: "ask-1",
    title: "Upload",
    detail: "xcrun altool --upload-app -f build/Wren-41.ipa -t ios",
    options: [{ option_id: "allow", label: "Allow once", option_kind: "allow_once" }]
  };
  it("wraps only at spaces: every word is one unbreakable piece", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => { } } });
    const words = [...body.matchAll(/<span class="c2-ask-word[^"]*">([^<]*)<\/span>/g)].map((match) => match[1]);
    expect(words).toEqual(["xcrun", "altool", "--upload-app", "-f", "build/Wren-41.ipa", "-t", "ios"]);
    expect(permission).toContain(".c2-ask-word { white-space: pre; }");
    expect(permission).toMatch(/\.c2-ask-detail \{[^}]*overflow-wrap: normal;/);
  });
});

describe("one red for Deny and the failed turn", () => {
  it("the Deny label and the failure mark are both #ff8f85", () => {
    expect(permission).toContain('.c2-ask-choice[data-conversation-ask-emphasis="decline"] :global(.cv-ask-label) { color: #ff8f85; }');
    expect(permission).not.toContain("#f08a7e");
    expect(tokens).toContain("--cv-failure-ink: #ff8f85;");
  });
});

describe("the command block in asks", () => {
  it("16px side padding, mono 12.5/20, still wraps only at spaces", () => {
    expect(permission).toContain(".c2-ask-detail { padding-inline: 16px; font-size: var(--cv-type-code); line-height: 20px; overflow-wrap: normal; }");
    expect(permission).toContain(".c2-ask-word { white-space: pre; }");
  });
});

describe("the permission ask's first label leads", () => {
  it("Allow once is #ffffff 500; Deny keeps #ff8f85", () => {
    expect(permission).toContain('.c2-ask-choice[data-conversation-ask-emphasis="primary"] :global(.cv-ask-label) { color: #ffffff; font-weight: 500; }');
    expect(permission).toContain('.c2-ask-choice[data-conversation-ask-emphasis="decline"] :global(.cv-ask-label) { color: #ff8f85; }');
  });
  it("only the first row carries the primary emphasis, and no row is preselected", () => {
    const ask = {
      askId: "ask-1",
      title: "Upload",
      detail: "xcrun altool --upload-app",
      options: [
        { option_id: "allow", label: "Allow once", option_kind: "allow_once" },
        { option_id: "always", label: "Always allow uploads", option_kind: "allow_always" },
        { option_id: "deny", label: "Deny", option_kind: "reject_once" }
      ]
    };
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => { } } });
    const emphases = [...body.matchAll(/data-conversation-ask-emphasis="(\w+)"/g)].map((match) => match[1]);
    expect(emphases).toEqual(["primary", "option", "decline"]);
    expect(body.indexOf('data-conversation-ask-emphasis="primary"')).toBeLessThan(body.indexOf("Allow once"));
    expect(body).not.toContain("is-selected");
  });
});
