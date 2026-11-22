import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const status = source("../src/components/conversation/TranscriptStatusRow.svelte");
const permission = source("../src/components/conversation/PermissionAskCard.svelte");

describe("v18 (1): no Waiting line in the transcript", () => {
  it("the pane has no waiting line, its state or its styles", () => {
    expect(pane).not.toContain("waitingLineShown");
    expect(pane).not.toContain("data-conversation-waiting-line");
    expect(pane).not.toContain("Waiting for you");
    expect(pane).not.toMatch(/\.c2-waiting-(edge|slot|dot)/);
  });
});

describe("v18 (2): the header's Dock button is the r22 filled pill", () => {
  it("#232323 fill, #d9d9d9 text at rest, #2a2a2a under the pointer, and no later rest rule", () => {
    const rest = ".chat-head-collapse { background: #232323; color: #d9d9d9; }";
    const hover = ".chat-head-collapse:hover, .chat-head-collapse:focus-visible { color: #ececec; background-color: #2a2a2a; }";
    expect(pane).toContain(rest);
    expect(pane).toContain(hover);
    expect(pane).not.toContain(".chat-head-collapse { background: transparent");
    expect(pane.slice(pane.indexOf(rest) + rest.length)).not.toMatch(/\.chat-head-collapse \{[^}]*background/);
  });
});

describe("v18 (3): links in answers back to v15", () => {
  it("the last link rule is v15's: #ececec with a 50% underline, #ffffff on hover; no accent", () => {
    const rest = app.match(/\[data-conversation-pane\] \.chat-thread \.markdown a:not\(\.file-preview-artifact-link, \.pv-act\) \{[^}]*\}/g) ?? [];
    const hover = app.match(/\[data-conversation-pane\] \.chat-thread \.markdown a:not\(\.file-preview-artifact-link, \.pv-act\):hover \{[^}]*\}/g) ?? [];
    expect(rest.at(-1)).toBe("[data-conversation-pane] .chat-thread .markdown a:not(.file-preview-artifact-link, .pv-act) { color: #ececec; text-decoration-color: rgb(236 236 236 / 50%); }");
    expect(hover.at(-1)).toBe("[data-conversation-pane] .chat-thread .markdown a:not(.file-preview-artifact-link, .pv-act):hover { color: #ffffff; text-decoration-color: #ececec; }");
  });
});

describe("v18 (4): one red for Deny and the failed turn", () => {
  it("the Deny label and the 'This turn failed' title are both #ff8f85", () => {
    expect(permission).toContain('.c2-ask-choice[data-conversation-ask-emphasis="decline"] :global(.cv-ask-label) { color: #ff8f85; }');
    expect(permission).not.toContain("#f08a7e");
    expect(status).toContain(".c2-status--callout.c2-status--error .c2-status-title { color: #ff8f85; }");
  });
  it("the Deny row still carries the declining emphasis", () => {
    const ask = {
      askId: "ask-1",
      title: "Upload",
      detail: "xcrun altool --upload-app",
      options: [
        { option_id: "allow", label: "Allow once", option_kind: "allow_once" },
        { option_id: "deny", label: "Deny", option_kind: "reject_once" }
      ]
    };
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } });
    expect(body).toMatch(/data-conversation-ask-emphasis="decline"/);
  });
});

describe("v18 (5): the command block in asks", () => {
  it("16px side padding, mono 12.5/20, still wraps only at spaces", () => {
    expect(permission).toContain(".c2-ask-detail { padding-inline: 16px; font-size: 12.5px; line-height: 20px; overflow-wrap: normal; }");
    expect(permission).toContain(".c2-ask-word { white-space: pre; }");
  });
});
