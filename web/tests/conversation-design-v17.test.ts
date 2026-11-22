import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import { NEWEST_LINE_BOTTOM_GAP_PIXELS } from "../src/components/conversation/viewport/viewportConfiguration";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const composer = source("../src/components/conversation/ConversationComposer.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const run = source("../src/components/conversation/ToolRunSummary.svelte");
const status = source("../src/components/conversation/TranscriptStatusRow.svelte");
const permission = source("../src/components/conversation/PermissionAskCard.svelte");
const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");

describe("v17 (1): inline code back to v15", () => {
  it("answers keep their chips; Full narration has no fill, mono 0.9em #dcdcdc, and nothing later overrides it", () => {
    const narration = "font-family: var(--cv-mono); font-size: 0.9em; line-height: inherit; padding: 0; border-radius: 0; background: none; color: #dcdcdc; }";
    expect(app).toContain(narration);
    expect(app.slice(app.indexOf(narration))).not.toMatch(/:not\(pre\) > code \{[^}]*background: #2a2a2a/);
  });
});

describe("v17 (2): disabled send back to v14", () => {
  it("#2a2a2a fill and a #9a9a9a arrow, in the composer and in the ask well", () => {
    expect(app).toContain(".chat-send { background: #2a2a2a; color: #9a9a9a; }");
    expect(app).not.toContain(".chat-send:not(.on, .stop)");
    expect(composer).toMatch(/:global\(\.chat-ask-send-round\) \{[^}]*background: #2a2a2a;\s*color: #9a9a9a;/);
    expect(composer).not.toContain(".chat-ask-send:not(.on) .chat-ask-send-round");
  });
});

describe("v17 (3): the live tool row keeps the settled row's icon", () => {
  it("no lit icon; Running #ececec 500 and the command #c4c4c4 stay", () => {
    expect(run).not.toMatch(/is-live \.c2-run-icon/);
    expect(run).toContain(".c2-run-summary.is-live .c2-run-verb { color: #ececec; font-weight: var(--cv-weight-label); }");
    expect(run).toContain(".c2-run-summary.is-live .c2-run-subject { color: #c4c4c4; }");
  });
});

describe("v17 (4): the error card", () => {
  it("keeps its v15 edge and the #ff8f85 title", () => {
    expect(status).not.toContain("border-color: #3a3a3a");
    expect(status).toContain(".c2-status--callout.c2-status--error .c2-status-title { color: #ff8f85; }");
  });
});

describe("v17 (5): the header's Dock button", () => {
  it("has no fill at rest, #d0d0d0 500, and fills #2a2a2a under the pointer (the rest rule comes first)", () => {
    const rest = ".chat-head-collapse { background: transparent; color: #d0d0d0; font-weight: 500; }";
    const hover = ".chat-head-collapse:hover, .chat-head-collapse:focus-visible { color: #ececec; background-color: #2a2a2a; }";
    expect(pane).toContain(rest);
    expect(pane).toContain(hover);
    expect(pane.indexOf(rest)).toBeLessThan(pane.indexOf(hover));
    expect(pane.slice(pane.indexOf(hover))).not.toMatch(/\.chat-head-collapse \{[^}]*background/);
  });
});

describe("v17 (6): the Waiting line", () => {
  it("shows while an ask waits, outside the dock, and not while activity is unknown", () => {
    expect(pane).toMatch(/let waitingLineShown = \$derived\(conversationState !== "rest" && !activityUncertain && !readOnly\s*&& \(ask !== null \|\| userInput !== null \|\| credentialWaiting \|\| waitingOnPage !== null\)\);/);
  });
  it("ends the transcript, before the queued messages: the accent dot, then Waiting for you", () => {
    const snippet = pane.slice(pane.indexOf("{#snippet waitingMessages()}"));
    expect(snippet.indexOf("data-conversation-waiting-line")).toBeGreaterThan(-1);
    expect(snippet.indexOf("data-conversation-waiting-line")).toBeLessThan(snippet.indexOf("<HeldPromptStack"));
    expect(snippet).toContain('<span class="c2-waiting-word">Waiting for you</span>');
  });
  it("is in the Working line's format: 24px above, 16px slot, 8px gap, 8px accent dot, 14/20 #c4c4c4", () => {
    expect(pane).toMatch(/\.c2-waiting-edge \{[^}]*gap: 8px;[^}]*margin-block-start: 24px;[^}]*color: #c4c4c4;[^}]*font: 400 14px\/20px var\(--cv-font\);/);
    expect(pane).toContain(".c2-waiting-slot { flex: none; display: inline-grid; place-items: center; width: 16px; height: 16px; }");
    expect(pane).toContain(".c2-waiting-dot { width: 8px; height: 8px; border-radius: 999px; background: var(--cv-accent); }");
  });
});

describe("v17 (7): the command block in asks", () => {
  const ask = {
    askId: "ask-1",
    title: "Upload",
    detail: "xcrun altool --upload-app -f build/Wren-41.ipa -t ios",
    options: [{ option_id: "allow", label: "Allow once", option_kind: "allow_once" }]
  };
  it("wraps only at spaces: every word is one unbreakable piece", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } });
    const words = [...body.matchAll(/<span class="c2-ask-word[^"]*">([^<]*)<\/span>/g)].map((match) => match[1]);
    expect(words).toEqual(["xcrun", "altool", "--upload-app", "-f", "build/Wren-41.ipa", "-t", "ios"]);
    expect(permission).toContain(".c2-ask-word { white-space: pre; }");
    expect(permission).toContain(".c2-ask-detail { padding-inline: 12px; overflow-wrap: normal; }");
  });
  it("stays mono 13/20", () => {
    expect(permission).toMatch(/\.c2-ask-detail \{[^}]*font: var\(--cv-weight-body\) 13px\/20px var\(--cv-mono\);/);
  });
});

describe("v17 (8): the transcript's end", () => {
  it("keeps 32px between its last item and the composer or ask card", () => {
    expect(viewport).toMatch(/\.chat-thread \{[^}]*padding-bottom: 32px;/);
    expect(NEWEST_LINE_BOTTOM_GAP_PIXELS).toBe(32);
  });
});

describe("v17 (8) leaves the empty state alone", () => {
  it("an empty thread keeps its 24px foot, so the heading stays 20px over the composer", () => {
    expect(viewport).toContain(".chat-thread:has(> .chat-thread-content > :global([data-conversation-empty])) { padding-bottom: 24px; }");
  });
});
