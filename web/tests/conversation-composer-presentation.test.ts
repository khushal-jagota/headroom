import { describe, expect, it } from "vitest";
import { render } from "svelte/server";

// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";

import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";

import { conversationState } from "../src/lib/conversation/dockPresentation";

import HeldPromptStack from "../src/components/conversation/composer/HeldPromptStack.svelte";
import { COMPOSER_PLACEHOLDER, composerPlaceholderFor } from "../src/lib/conversation/runControls/logic/runSelection";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";

import ComposerWithQueryClient from "./fixtures/ComposerWithQueryClient.svelte";

import { ASK_REPLY_PLACEHOLDER } from "../src/lib/conversation/runControls/logic/runSelection";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const picker = source("../src/components/conversation/UnifiedModelPicker.svelte");
const listbox = source("../src/components/conversation/ListboxPicker.svelte");
const app = source("../../assets/app.css");
const controls = source("../src/components/conversation/composer/ComposerRunControls.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
const mode = source("../src/components/conversation/composer/DeliveryModePicker.svelte");

describe("composer", () => {
  it("menus open with their bottom 8px above the composer's top edge", () => {
    expect(composer).toContain('anchor.style.setProperty("--c2-menu-lift"');
    expect(composer).toContain("bottom: calc(100% + 8px + var(--c2-menu-lift, 0px));");
    expect(composer).toContain(".chat-add-menu { inset-block-end: calc(100% + 8px + var(--c2-menu-lift, 0px));");
  });
});

describe("model picker", () => {
  it("the composer uses the base picker: a chosen model goes on to its efforts, Reasoning stays in the rail", () => {
    expect(picker).not.toContain("layout");
    expect(picker).toContain('showing = "efforts";');
    expect(picker).toContain('"data-conversation-picker-reasoning": ""');
    expect(picker).not.toContain("model-picker-effort-control");
    expect(source("../src/components/conversation/composer/ComposerRunControls.svelte")).not.toContain('layout="composer"');
  });
  it("only the Steer menu (compact) wears the composer's floating plane; the rail panel keeps the base look", () => {
    expect(listbox).not.toMatch(/\[data-conversation-composer\]\) \.listbox-picker-panel \{/);
    expect(listbox).not.toMatch(/\[data-conversation-composer\]\) \.listbox-picker-option \{/);
    expect(listbox).toContain(":global([data-conversation-composer]) .listbox-picker-panel.compact {");
    expect(listbox).toContain('{item.value === selectedValue ? "✓" : ""}');
    expect(source("../src/components/conversation/composer/DeliveryModePicker.svelte")).toContain("drawnTick");
  });
  it("the composer's model picker panel is only as wide as its content", () => {
    expect(listbox).toMatch(/\[data-conversation-composer\]\) \.listbox-picker-panel\.rail \{\s*grid-template-columns: max-content max-content;\s*width: max-content;/);
    expect(listbox).not.toContain("width: 440px");
  });
});

describe("colour and motion", () => {
  it("the composer grows into an ask over 220 ms and its contents fade in over 120 ms", () => {
    expect(composer).toContain('{ duration: 220, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" }');
    expect(composer).toContain("[{ opacity: 0 }, { opacity: 1 }], { duration: 120, easing: \"ease-out\" }");
    expect(composer).toContain('window.matchMedia("(prefers-reduced-motion: reduce)").matches');
  });
});

describe("messages and asks", () => {

  it("the placeholder is Write a message everywhere; the ask wells keep theirs", () => {
    expect(COMPOSER_PLACEHOLDER).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: null, mode: "queue", running: false, base: "Message Chief of Staff..." })).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: "answers", mode: "queue", running: true, base: "x" })).toBe("Or write a reply");
    expect(composerPlaceholderFor({ askWaiting: "holds", mode: "queue", running: true, base: "x" })).toBe("Or write a reply");
  });
});

describe("no chevrons on dropdown or expand controls", () => {
  it("the model, Steer and Queued chips show their label alone; Close has no arrow, only the dock's Open keeps one", () => {
    expect(source("../src/components/conversation/composer/ComposerRunControls.svelte")).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/DeliveryModePicker.svelte")).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).not.toContain("chat-qrow-chevron");
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    const head = pane.slice(pane.indexOf('class="chat-head-collapse"'), pane.indexOf("</button>", pane.indexOf('class="chat-head-collapse"')));

    expect(head).not.toContain("<svg");
    const rest = render(ConversationRestBar, { props: { onOpen: () => { }, status: { kind: "working", clock: null } } }).body;
    const control = rest.slice(rest.indexOf("c2-rest-control"));

    expect(control).toMatch(/c2-rest-control-label[^>]*>Open<\/span>[\s\S]*?<svg class="c2-rest-control-arrow/);
    // Pager arrows, Details › and Ran 2 commands › are not dropdown marks and stay.
    expect(source("../src/components/conversation/ToolRunSummary.svelte")).toContain('class="c2-run-chevron"');
    expect(source("../src/components/conversation/TranscriptStatusRow.svelte")).toContain("c2-status-chevron");
  });
});

describe("thread pieces", () => {
  it("no ask draws a Stop; the ask cards get no onStop either", () => {
    expect(composer).not.toContain('{#if panelTakesTheBox && running && onStop}');
    expect(composer).not.toContain("chat-ask-stop");
    const question = composer.slice(composer.indexOf("<UserInputQuestionPanel"), composer.indexOf("/>", composer.indexOf("<UserInputQuestionPanel")));
    expect(question).toContain("onStop={undefined}");
    const permission = composer.slice(composer.indexOf("<PermissionAskCard"), composer.indexOf("/>", composer.indexOf("<PermissionAskCard")));
    expect(permission).toContain("onStop={undefined}");
    expect(composer).toContain("<ConversationCredentialRequestCard request={credentialRequest} framed={false} onStop={undefined} />");
  });
});

describe("asks", () => {
  it("the send slot under an ask keeps the round send; its disabled state lifts for a several-answer question with a choice", () => {
    expect(controls).toContain("{#if asking || view.submit.active || view.submit.sending || !view.showStop}");
    expect(controls).toContain('class={`chat-send${view.submit.active || askCommitReady ? " on" : ""}`}');
    expect(controls).toContain("disabled={view.submit.disabled && !askCommitReady}");
    expect(controls).toContain("{#if !asking && view.showStop}");
    expect(composer).toContain('askCommitReady={userInput !== null && questionCommitReady && text.trim() === "" && attachments.length === 0}');
    expect(composer).toMatch(/if \(userInput !== null && questionPanel !== null && questionCommitReady && text\.trim\(\) === "" && attachments\.length === 0\) \{\s*questionPanel\.commit\(\);\s*return;\s*\}/);
  });
});

describe("queued notes", () => {
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
  it("a status line (no pill): clock, Queued #ececec 500, ' · ' and 'n of m' #c4c4c4, then a 28px more button", () => {
    const html = render(HeldPromptStack, { props: { rows: [row(), row({ key: "h2", heldPromptId: "hp-2" })], onEdit: () => { } } }).body;
    expect(html).toContain("1 of 2");
    expect(html).toContain("2 of 2");
    expect(html.match(/data-conversation-held-more/g)?.length).toBe(2);
    expect(html).toMatch(/<span class="chat-qrow-note[^"]*" data-conversation-held-chip/);
    expect(held).toContain(".chat-qrow-word { color: #ececec; font-weight: var(--cv-weight-label); }");
    expect(held).toContain(".chat-qrow-detail { color: #c4c4c4;");
    expect(held).toMatch(/\.chat-qrow-more \{[^}]*width: 28px;\s*height: 28px;[^}]*background: none;/);
  });
  it("the menu offers Send now, Edit and Remove, in that order", () => {
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
  it("Edit is offered only for a words-only message the server holds (a picture cannot go back as it was sent)", () => {
    const picture = row({ content: [{ piece: "image", media_type: "image/png", stored_file_id: "f", file_name: "chart.png" } as never, { piece: "text", text: "Match this." }] });
    expect(render(HeldPromptStack, { props: { rows: [picture], onEdit: () => { } } }).body).not.toContain("data-conversation-held-edit");
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

describe("the composer", () => {
  it("8px between each control on the right (model, Steer, mic, Send/Stop)", () => {
    expect(composer).toContain(".chat-box:not(.has-ask) > .chat-foot > :global(.chat-submit) { gap: var(--cv-s2); }");
    expect(composer).toContain(".chat-box:not(.has-ask) > .chat-foot > :global([data-conversation-model-picker]) { margin-inline-end: var(--cv-s1); }");
  });
});

describe("composer attachment chips", () => {
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

describe("one Stop style everywhere", () => {
  const base = { draftKey: "composer-presentation", onSend: async () => true };
  it("is the composer's last control while the agent works; the active Send stays #f2f2f2", () => {
    const body = render(ComposerWithQueryClient, { props: { ...base, running: true, onStop: () => { } } }).body;
    expect(body).toMatch(/<button[^>]*class="chat-send stop"[^>]*data-conversation-stop/);
    expect(controls).toContain('class="chat-send stop"');
    expect(app).toContain(".chat-send.on { background: #f2f2f2; color: #121212; }");
  });
});

describe("asks have no Stop; the header menu leads with Stop the agent", () => {
  const base = { draftKey: "composer-presentation", onSend: async () => true };
  const ask = {
    askId: "a-1", title: "Upload build 41", detail: "xcrun altool --upload-app", options: [
      { option_id: "allow", label: "Allow once", option_kind: "allow_once" as const },
      { option_id: "deny", label: "Deny", option_kind: "reject_once" as const }]
  };
  it("an ask's bottom row is the answer well only: draft, microphone, send; no pen", () => {
    const body = render(ComposerWithQueryClient, { props: { ...base, running: true, ask, onStop: () => { }, onCancelTurn: () => { } } }).body;
    expect(body).not.toContain("data-conversation-stop");
    expect(body).not.toContain("chat-ask-stop");
    expect(body).not.toContain("chat-ask-pen");
    expect(body).toMatch(/class="chat-send chat-ask-send/);
    expect(composer).not.toContain("chat-ask-stop");
  });
});

describe("the grown composer starts at its own inner padding", () => {
  it("drops the 41px indent of the draft and the chips", () => {
    expect(composer).not.toContain("padding-inline-start: 41px");
  });
});

describe("the attachment remove button", () => {
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

describe("asks", () => {

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

describe("ask spacing", () => {
  it("16px between the last option and the answer well", () => {
    expect(composer).toMatch(/\.chat-box > \.chat-ask \{[^}]*margin-block-end: var\(--cv-s4\);/);
  });
});

describe("attachment chips", () => {
  it("a picture has a 28px top scrim under the remove button; a file chip ends with 40px", () => {
    expect(composer).toContain("background: linear-gradient(rgb(0 0 0 / 55%), transparent) top / 100% 28px no-repeat;");
    expect(composer).toContain("padding-inline: 12px 40px;");
  });
});

describe("the mode menu fits its content", () => {
  it("no minimum width, 12px label padding, 16px to the check, unselected #e0e0e0", () => {
    expect(mode).toMatch(/width: max-content;\s*min-width: 0;/);
    expect(mode).toMatch(/\.listbox-picker-option\) \{\s*gap: 16px;\s*padding-inline: 12px;\s*color: #e0e0e0;/);
  });
});

describe("the queued more button", () => {
  it("is a 28px target, icon #c4c4c4, hover #2a2a2a", () => {
    expect(held).toContain(".chat-qrow-more { width: 28px; height: 28px; color: #c4c4c4; }");
    expect(held).toContain(".chat-qrow-more:hover:not(:disabled) { background: #2a2a2a; }");
  });
});

describe("the grown composer", () => {
  it("has the same 12px padding at the bottom as at the top", () => {
    expect(composer).toContain(".chat-box:not(.is-compact, .has-ask) { padding-block-end: var(--cv-s3); }");
    expect(app).toMatch(/\.chat-box \{[^}]*padding: var\(--cv-s3\) var\(--cv-s2\) var\(--cv-s2\);/);
  });
});
