import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const permission = source("../src/components/conversation/PermissionAskCard.svelte");

const lastRule = (css: string, selector: string): string | undefined => {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return css.match(new RegExp(`(^|\\n)${escaped} \\{[^}]*\\}`, "g"))?.at(-1)?.trim();
};

describe("v19 (1): an open ask keeps a short transcript at the foot of the room above it", () => {
  it("the transcript region says when a question or a permission ask is open", () => {
    expect(pane).toContain("data-conversation-ask-open={!readOnly && (ask !== null || userInput !== null) ? true : undefined}");
  });
  it("the content box is pushed to the thread's end (its 32px bottom padding is the gap to the card)", () => {
    expect(pane).toContain(".conversation-transcript-region[data-conversation-ask-open] :global(.chat-thread-content) { margin-block-start: auto; }");
    const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");
    expect(viewport).toMatch(/\.chat-thread \{[^}]*padding-bottom: 32px;/);
  });
});

describe("v19 (2): Stop in the composer is the one white control", () => {
  it("a #ececec round with no ring and a 10px #141414 square, radius 2", () => {
    expect(lastRule(app, ".chat-send.stop")).toBe(".chat-send.stop { background: #ececec; box-shadow: none; color: #141414; }");
    expect(lastRule(app, ".chat-send.stop .chat-send-fill")).toBe(".chat-send.stop .chat-send-fill { width: 10px; height: 10px; border-radius: 2px; }");
    expect(app).toMatch(/\.chat-send \{[^}]*width: 32px; height: 32px;/);
  });
  it("the disabled send stays #2a2a2a with a #9a9a9a arrow", () => {
    expect(lastRule(app, ".chat-send")).toBe(".chat-send { background: #2a2a2a; color: #9a9a9a; }");
  });
});

describe("v19 (3): the permission ask's first label leads", () => {
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
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } });
    const emphases = [...body.matchAll(/data-conversation-ask-emphasis="(\w+)"/g)].map((match) => match[1]);
    expect(emphases).toEqual(["primary", "option", "decline"]);
    expect(body.indexOf('data-conversation-ask-emphasis="primary"')).toBeLessThan(body.indexOf("Allow once"));
    expect(body).not.toContain("is-selected");
  });
});
