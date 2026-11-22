import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";

import { SvelteSet } from "svelte/reactivity";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";

import { conversationState } from "../src/lib/conversation/dockPresentation";

import type { ConversationViewItem } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

import LiveEdge from "../src/components/conversation/view/LiveEdge.svelte";

import type { ConversationViewStatus, ConversationViewTurn } from "../src/lib/conversation/view/contracts";

import { dockEdgeOpacity, DOCK_EDGE_FADE_MS } from "../src/lib/conversation/layout/dock";
import { CONVERSATION_CARD_MS } from "../src/lib/conversation/surfaceMotion";

import { fitTitle, TITLE_ELLIPSIS } from "../src/lib/conversation/titleFit";

import PaneWithQueryClient from "./fixtures/PaneWithQueryClient.svelte";

import { NEWEST_LINE_BOTTOM_GAP_PIXELS } from "../src/components/conversation/viewport/viewportConfiguration";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const live = source("../src/components/conversation/LiveConversation.svelte");

describe("a docked conversation shows a waiting ask as its composer", () => {
  const css = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
  it("the pane always draws the composer, ask included, whether the conversation is at rest or opened", () => {
    const pane = css("../src/components/conversation/ConversationPane.svelte");
    // The composer is gated only by read-only, never by the dock state, and it is handed the
    // ask, the question and the key request, so at rest the ask is the dock's composer.
    const composerBlock = pane.slice(pane.indexOf("{#if readOnly}\n    <div class=\"conversation-read-only\""), pane.indexOf("<ConversationComposer"));
    expect(composerBlock).not.toContain("conversationState");
    const composer = pane.slice(pane.indexOf("<ConversationComposer"), pane.indexOf("/>", pane.indexOf("<ConversationComposer")));
    expect(composer).toContain("{ask}");
    expect(composer).toContain("{userInput}");
    expect(composer).toContain("{credentialRequest}");
    // Nothing hides the ask in the dock.
    expect(css("../../assets/app.css")).not.toMatch(/dock-state[^{]*\{[^}]*\}[^{]*data-ask-shell|data-ask-shell[^{]*\{[^}]*display:\s*none/);
  });
});

describe("the dock line under which an ask waits", () => {
  const facts = { readOnly: false, activityUncertain: false, waitingForYou: true, waitingFor: "approval" as const, running: true, backgroundWorking: false, settingUp: null, elapsed: null };
  const draw = (props: Record<string, unknown>) =>
    render(ConversationRestBar, { props: { onOpen: () => { }, state: conversationState(facts), waitingTitle: "Upload Wren build 41 to TestFlight", ...props } }).body;
  it("carries no status text when an ask is the composer under it: only Open conversation", () => {
    for (const pageHoldsTheAsk of [false, true]) {
      const html = draw({ askBelow: true, pageHoldsTheAsk, preview: "An unread line" });
      expect(html).toContain("Open conversation");
      expect(html).not.toContain("data-conversation-rest-line");
      expect(html).not.toContain("Needs your approval");
      expect(html).not.toContain("Waiting for your approval");
      expect(html).not.toContain("Upload Wren build 41");
      expect(html).not.toContain("cv-dot");
      expect(html).not.toContain("An unread line");
    }
  });
  it("keeps its line when no ask is below: a proposal alone says Needs your approval", () => {
    expect(draw({ pageHoldsTheAsk: true })).toContain("Needs your approval");
    expect(draw({ pageHoldsTheAsk: true })).not.toContain("Waiting for your approval");
    expect(draw({})).toContain("Needs your approval");
  });
  it("the pane says an ask is below whenever a permission ask, a question or a key request waits", () => {
    expect(source("../src/components/conversation/ConversationPane.svelte")).toContain("askBelow={ask !== null || userInput !== null || credentialWaiting}");
  });
});

describe("conversation geometry", () => {
  it("the full conversation is one 760px column; docked on a work page it keeps the page's column", () => {
    expect(tokens).toContain("--cv-col: 760px;");
    expect(app).not.toMatch(/\n\.conversation-layer > \.conversation-column \{\s*width: min\(var\(--cv-col\)/);
    expect(app).toMatch(/\.ticket-head,\n\.ticket-col,\n\.conversation-column,\n\.manager-column \{\s*width: min\(var\(--page-measure\), calc\(100% - 2 \* var\(--space-5\)\)\);/);
    expect(app).toMatch(/\[data-conversation-dock-state="opened"\] > \.conversation-layer > \.conversation-column \{\s*width: min\(var\(--cv-col\), calc\(100% - 2 \* var\(--space-5\)\)\);/);
  });
});

describe("conversation geometry", () => {
  it("the header carries the title alone, never the role", () => {
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    expect(pane).toContain('<div class="chat-head-identity" data-conversation-head-identity>');
    expect(pane).not.toContain("data-conversation-subtitle");
  });
});

describe("conversation geometry", () => {
  it("under the solid header a 40px fade starts right at its bottom edge", () => {
    expect(viewport).toContain("--c2-top-fade: transparent 0, rgb(0 0 0 / 0.35) 16px, #000 40px;");
  });
  it("while newer content is below the view, the bottom edge dissolves over 16px above the composer", () => {
    expect(viewport).toContain("--c2-bottom-fade: #000 calc(100% - 16px), transparent 100%;");
    expect(viewport).toMatch(/\.chat-thread\[data-more-below="true"\] \{[^}]*mask-image: linear-gradient\(to bottom, var\(--c2-top-fade\), var\(--c2-bottom-fade\)\);/);
  });
  it("24px before the lens control", () => {
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    expect(pane).toMatch(/\.chat-head-identity \{[^}]*margin-inline-end: 12px;/);
  });
});

describe("controls", () => {
  it("it shows only when the newest content is more than one viewport below the visible area", async () => {
    const { jumpToLatestWanted } = await import("../src/components/conversation/viewport/threadGeometry");
    expect(jumpToLatestWanted(0, 600)).toBe(false);
    expect(jumpToLatestWanted(300, 600)).toBe(false);
    expect(jumpToLatestWanted(600, 600)).toBe(false);
    expect(jumpToLatestWanted(601, 600)).toBe(true);
    expect(jumpToLatestWanted(5000, 0)).toBe(false);
    const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");
    expect(viewport).toContain("const jumpShown = $derived(newestLineFarBelow || newerHistory || newerThanUnplacedAnchor || latestPending);");
  });
  it("dropdowns fit their longest label : the mode menu has no minimum); the composer's open 8px above the composer's top edge", () => {
    const mode = source("../src/components/conversation/composer/DeliveryModePicker.svelte");
    expect(mode).toMatch(/bottom: calc\(100% \+ 8px \+ var\(--c2-menu-lift, 0px\)\);\s*width: max-content;\s*min-width: 0;/);
    const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
    expect(held).toMatch(/bottom: calc\(100% \+ 8px\);\s*width: max-content;\s*min-width: 160px;/);
  });
  it("an open chip (Steer/Queue, model, Queued) is #3a3a3a with #f2f2f2 words; no chevron", () => {
    expect(app).toContain("[data-conversation-composer] .chat-foot [data-conversation-model-picker] > .listbox-picker-trigger.is-open:hover:not(:disabled) { position: relative; z-index: 41; background: #3a3a3a; color: #f2f2f2; }");
    const mode = source("../src/components/conversation/composer/DeliveryModePicker.svelte");
    expect(mode).toMatch(/trigger\.is-open:hover:not\(:disabled\)\) \{\s*background: #3a3a3a;\s*color: #f2f2f2;/);
    expect(mode).toMatch(/background: #3a3a3a;\s*color: #f2f2f2;\s*\/\*[^*]*\*\/\s*position: relative;\s*z-index: 41;/);
    const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");

    expect(held).toContain('.chat-qrow-more[aria-expanded="true"], .chat-qrow-more[aria-expanded="true"]:hover:not(:disabled) { position: relative; z-index: 31; background: #3a3a3a; color: #f2f2f2; }');
    expect(held).not.toContain("chat-qrow-chevron");
    expect(mode).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/ComposerRunControls.svelte")).toContain('chevron="none"');
  });
});

describe("surfaces", () => {
  it("the dock has no surface of its own; it sits on the canvas with its canvas fade", () => {
    expect(app).not.toContain("#161616");
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain("background:linear-gradient(to bottom, transparent, var(--cv-canvas));");
  });
});

describe("layout", () => {
  it("threads are top-anchored again; the empty line is centred; the lens toggle always shows", () => {
    expect(viewport).toContain(".chat-thread-content { margin-block-start: 0; }");
    expect(viewport).toContain(".chat-thread-content:has(> :global([data-conversation-empty])) { flex: 1 0 auto; justify-content: center; }");
    const live = source("../src/components/conversation/LiveConversation.svelte");
    expect(live).toContain(".conversation-empty { display: flex; align-items: center; justify-content: center; flex: 1 0 auto; align-self: stretch; }");
    expect(live).not.toContain("lensHidden");
    expect(source("../src/components/conversation/ConversationPane.svelte")).not.toContain("lensHidden");
  });
  it("when the composer grows, a transcript that was at its end stays at its end", () => {
    expect(viewport).toContain("if (shrank && endGapBeforeResize <= 1 && heldSent === null && !pendingOpen && placementReady)");
    expect(viewport).toContain("moveTheThread(thread, thread.scrollHeight);");
    expect(viewport).toContain("endGapBeforeResize = t.scrollHeight - t.scrollTop - t.clientHeight;");
  });
});

describe("the dock", () => {
  const turn: ConversationViewTurn = {
    kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
    settled: false, stopped: false, started_at_unix_milliseconds: 100_000, ending: null,
    duration_seconds: null, tool_call_count: 1, folded_message_count: 0, latest_message_key: null, is_latest: true
  };
  const runningTool: ConversationViewItem = {
    kind: "row", key: "tool:running", row_kind: "tool_call", order: { sequence: 2, slot: 1 }, source_sequence: 2,
    sender_message_id: null, changed_through_sequence: 2, created_at: 0, turn_key: "turn:e1", work_group_key: "g1", fold_rule: "work",
    body: { kind: "inline", value: { kind: "tool_call", tool_call_id: "t1", title: "Run go test", tool_kind: "execute", detail: null, started_detail: null, capped_detail_sequence: null, status: "running" } }
  };
  const status = { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, stopped_without_ending: false } as unknown as ConversationViewStatus;
  const reader = (lens: "focus" | "full"): ConversationViewReader => ({
    id: "c", lens, pages: [{ scope: { kind: "timeline", key: null }, items: [turn, runningTool] }], status, openTools: new SvelteSet<string>()
  } as unknown as ConversationViewReader);
  const workingFacts = { readOnly: false, activityUncertain: false, waitingForYou: false, waitingFor: null, running: true, backgroundWorking: false, settingUp: null, elapsed: "2m 14s" };
  it("no narration sentence under the Working line, in the dock or the transcript", () => {
    const html = render(ConversationRestBar, { props: { onOpen: () => { }, state: conversationState(workingFacts as never) } }).body;
    expect(html).not.toContain("narration");
    expect(pane).not.toContain("live_narration");
    expect(render(LiveEdge, { props: { reader: reader("focus") } }).body).not.toContain("narration");
    expect(source("../src/lib/conversation/view/contracts.ts")).not.toContain("live_narration");
  });
  it("Open conversation has a 12px up arrow after its label", () => {
    const html = render(ConversationRestBar, { props: { onOpen: () => { }, state: conversationState(workingFacts as never) } }).body;
    expect(html).toMatch(/>Open conversation<\/span>[\s\S]*?<svg class="c2-rest-control-arrow[^"]*" viewBox="0 0 16 16" data-conversation-open-arrow=""><path d="m4 10 4-4 4 4"><\/path><\/svg>/);
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain(".c2-rest-control-arrow { flex:none; width:12px; height:12px;");
  });
  it("with a proposal on the page, Needs your approval wears its dot; in #bccbf0 like every ask label", () => {
    const bar = source("../src/components/conversation/ConversationRestBar.svelte");
    expect(bar).toContain("[data-conversation-rest-page-waits-row] .c2-rest-label { color:#bccbf0; }");
    expect(bar).toContain("[data-conversation-rest-page-waits-row] .cv-dot { width:6px; height:6px; margin-inline:0; background:#bccbf0; }");
    expect(bar).not.toContain("#a9bbe3");
  });
  it("the edge fades over 180ms: out at the start of opening, in over the end of the return", () => {
    expect(DOCK_EDGE_FADE_MS).toBe(180);
    const at = (ms: number) => ms / CONVERSATION_CARD_MS;
    // Opening: gone 180ms in, half gone at 90ms.
    expect(dockEdgeOpacity(1, 0, 0)).toBe(1);
    expect(dockEdgeOpacity(1, 0, at(90))).toBeCloseTo(0.5);
    expect(dockEdgeOpacity(1, 0, at(180))).toBe(0);
    expect(dockEdgeOpacity(1, 0, 1)).toBe(0);
    // Returning: nothing while the layer is still tall, fully there as the dock lands.
    const start = CONVERSATION_CARD_MS - 180;
    expect(dockEdgeOpacity(0, 1, at(start))).toBe(0);
    expect(dockEdgeOpacity(0, 1, at(start + 90))).toBeCloseTo(0.5);
    expect(dockEdgeOpacity(0, 1, 1)).toBe(1);
  });
});

describe("the header", () => {
  it("the title is 17/24 600, ends in an ellipsis, and keeps at least 24px (12 margin + 12 gap) before Focus/Full", () => {
    expect(pane).toMatch(/\.chat-head-title \{[^}]*overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;[^}]*font: var\(--cv-weight-title\) 17px\/24px var\(--cv-font\);/);
    expect(pane).toMatch(/\.chat-head-identity \{[^}]*margin-inline-end: 12px;/);
    expect(app).toMatch(/\.chat-head \{[^}]*gap: var\(--cv-s3\);/);
  });
});

describe("a truncated title has no space before the ellipsis", () => {
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

describe("asks have no Stop; the header menu leads with Stop the agent", () => {
  it("while a turn is open the menu's first item is Stop the agent, which stops through onStop", () => {
    const actions = pane.slice(pane.indexOf('<div class="chat-overflow-actions" role="menu"'));
    const first = actions.indexOf("{#if running && onStop}");
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(actions.indexOf("{#if confirmArmed}"));
    expect(actions).toMatch(/data-conversation-menu-stop\s+onclick=\{stopFromTheMenu\}\s*>Stop the agent<\/button>/);
    expect(pane).toMatch(/function stopFromTheMenu\(\): void \{\s*closeMenu\(\);\s*onStop\?\.\(\);/);
  });
});

describe("the empty conversation (B8)", () => {
  it("says 'No messages yet' as one centred system line, with the composer in its usual place", () => {
    expect(live).toContain('<SystemLine align="center">No messages yet</SystemLine>');
    expect(live).not.toContain("What should we work on?");
    expect(live).not.toContain("You can attach or drop files here.");
    expect(pane).not.toContain("emptyHint");
    expect(pane).not.toContain("is-empty-start");
  });
});

describe("the Focus/Full control", () => {
  it("the other lens is #d0d0d0 400; the shown one #424242 with #ffffff 500", () => {
    expect(pane).toContain(".cv-segment { color: #d0d0d0; }");
    expect(pane).toContain('.cv-segment[aria-pressed="true"], .cv-segment[aria-pressed="true"]:hover { background: #424242; color: #ffffff; }');
  });
});

describe("the transcript's end", () => {
  it("keeps 32px between its last item and the composer or ask card", () => {
    expect(viewport).toMatch(/\.chat-thread \{[^}]*padding-bottom: 32px;/);
    expect(NEWEST_LINE_BOTTOM_GAP_PIXELS).toBe(32);
  });
});

describe("no Waiting line in the transcript", () => {
  it("the pane has no waiting line, its state or its styles", () => {
    expect(pane).not.toContain("waitingLineShown");
    expect(pane).not.toContain("data-conversation-waiting-line");
    expect(pane).not.toContain("Waiting for you");
    expect(pane).not.toMatch(/\.c2-waiting-(edge|slot|dot)/);
  });
});

describe("the header's Dock button is the r22 filled pill", () => {
  it("#232323 fill, #d9d9d9 text at rest, #2a2a2a under the pointer, and no later rest rule", () => {
    const rest = ".chat-head-collapse { background: #232323; color: #d9d9d9; }";
    const hover = ".chat-head-collapse:hover, .chat-head-collapse:focus-visible { color: #ececec; background-color: #2a2a2a; }";
    expect(pane).toContain(rest);
    expect(pane).toContain(hover);
    expect(pane).not.toContain(".chat-head-collapse { background: transparent");
    expect(pane.slice(pane.indexOf(rest) + rest.length)).not.toMatch(/\.chat-head-collapse \{[^}]*background/);
  });
});

describe("an open ask keeps a short transcript at the foot of the room above it", () => {
  it("the transcript region says when a question or a permission ask is open", () => {
    expect(pane).toContain("data-conversation-ask-open={!readOnly && (ask !== null || userInput !== null) ? true : undefined}");
  });
  it("the content box is pushed to the thread's end (its 32px bottom padding is the gap to the card)", () => {
    expect(pane).toContain(".conversation-transcript-region[data-conversation-ask-open] :global(.chat-thread-content) { margin-block-start: auto; }");
    const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");
    expect(viewport).toMatch(/\.chat-thread \{[^}]*padding-bottom: 32px;/);
  });
});
