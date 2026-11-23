import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import ComposerWithQueryClient from "./fixtures/ComposerWithQueryClient.svelte";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";

const base = { draftKey: "conversation-composer", onSend: async () => true };
const ask = {
  askId: "a-1", title: "Upload build 41", detail: "xcrun altool --upload-app", options: [
    { option_id: "allow", label: "Allow once", option_kind: "allow_once" as const },
    { option_id: "deny", label: "Deny", option_kind: "reject_once" as const }]
};

function held(key: string, text: string): HeldPromptRow {
  return {
    key, heldPromptId: `hp-${key}`, senderMessageId: null, content: [{ piece: "text", text }],
    senderLabel: "owner", sentAtUnixMilliseconds: 1, state: "held", queueReason: null
  };
}

describe("the composer's controls row", () => {
  it("is one line at rest, with add, the model, voice and send in that one row", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/class="chat-box[^"]*is-compact/);
    expect(body).toContain("data-conversation-add");
    expect(body).toContain("data-conversation-model-picker");
  });

  it("always ends in a send round, quiet and unavailable while there is nothing to send", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/<button[^>]*class="chat-send"[^>]*data-conversation-send/);
    expect(body).toMatch(/data-conversation-send[^>]*disabled|disabled[^>]*data-conversation-send/);
    expect(body).not.toContain("data-conversation-stop");
  });

  it("offers Stop turn while a turn runs, on the composer's one line", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true } });
    expect(body).toMatch(/data-conversation-stop[^>]*aria-label="Stop turn"|aria-label="Stop turn"[^>]*data-conversation-stop/);
    expect(body).not.toContain("data-conversation-send=");
    expect(body).toMatch(/class="chat-box[^"]*is-compact/);
    expect(body).toContain('data-conversation-box-shape="line"');
  });

  it("makes the round Stop its last control while a turn works with nothing waiting on the person", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true, onStop: () => {} } });
    expect(body).toMatch(/<button[^>]*class="chat-send stop"[^>]*data-conversation-stop/);
    expect(body).not.toContain("data-conversation-ask-stop");
    expect(body.match(/data-conversation-stop/g)?.length).toBe(1);
  });

  it("shows the model and delivery mode chips as their labels alone, with no chevron", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true, supportsSteer: true } });
    expect(body).toContain("data-conversation-model-picker");
    expect(body).toContain("data-conversation-send-mode");
    expect(body).not.toContain("listbox-picker-chevron");
  });

  it("says only Drop to attach while something is dragged over it", () => {
    const { body } = render(ComposerWithQueryClient, { props: base });
    expect(body).toMatch(/data-conversation-drop-label[\s\S]*Drop to attach/);
  });
});

describe("a question asked in the composer's place", () => {
  it("has no confirm pill and no Stop; the answer well keeps its round send, drawn inside it", () => {
    const userInput = { requestId: "q-1", questions: [
      { question_id: "a", header: "Style", question: "Which style?", multi_select: false, allow_other: true, options: [{ label: "Soft", description: "" }] },
      { question_id: "b", header: "Tone", question: "Which tone?", multi_select: false, allow_other: false, options: [{ label: "Warm", description: "" }] }
    ] };
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true, userInput, onStop: () => {} } });
    expect(body).not.toContain("data-conversation-ask-primary");
    expect(body).toMatch(/<button[^>]*class="chat-send[^"]*chat-ask-send[^"]*"[^>]*data-conversation-send[^>]*disabled/);
    expect(body).not.toContain("data-conversation-stop");
    expect(body).not.toContain("chat-ask-stop");
    expect(body).not.toContain(">Stop turn<");
    // The send is drawn inside the well: its round is a span in the button.
    expect(body).toMatch(/class="chat-send chat-ask-send[^"]*"[^>]*>(<!--[^>]*-->)*<span class="chat-ask-send-round"/);
  });

  it("sends a several-answer question that has a choice and no words of its own", () => {
    // The tests run without a DOM; the wiring is read from the source.
    const composer = readFileSync(new URL("../src/components/conversation/ConversationComposer.svelte", import.meta.url), "utf8") as string;
    const controls = readFileSync(new URL("../src/components/conversation/composer/ComposerRunControls.svelte", import.meta.url), "utf8") as string;
    expect(composer).toContain('askCommitReady={userInput !== null && questionCommitReady && text.trim() === "" && attachments.length === 0}');
    expect(composer).toMatch(/if \(userInput !== null && questionPanel !== null && questionCommitReady && text\.trim\(\) === "" && attachments\.length === 0\) \{\s*questionPanel\.commit\(\);\s*return;\s*\}/);
    expect(controls).toContain("disabled={view.submit.disabled && !askCommitReady}");
  });
});

describe("a permission ask in the composer's place", () => {
  it("draws no Stop, no pen and no mode chip; the well keeps its round send and says Or write a reply", () => {
    const { body } = render(ComposerWithQueryClient, { props: { ...base, running: true, ask, onStop: () => {}, onCancelTurn: () => {} } });
    // The ask's bottom row is the answer well only; the header's menu stops the agent.
    expect(body).not.toContain("data-conversation-stop");
    expect(body).not.toContain("chat-ask-stop");
    expect(body).not.toContain("cv-ask-stop");
    expect(body).not.toContain(">Stop turn<");
    expect(body).not.toContain("data-conversation-ask-primary");
    expect(body).not.toContain("data-conversation-send-mode");
    expect(body).toMatch(/<button[^>]*class="chat-send[^"]*chat-ask-send[^"]*"[^>]*data-conversation-send[^>]*disabled/);
    expect(body).not.toContain("chat-ask-pen");
    expect(body).not.toContain('data-conversation-ask-action="cancel_turn"');
    expect(body).toContain('placeholder="Or write a reply"');
  });
});

describe("queued messages and the composer", () => {
  it("never draws the queue over the composer", () => {
    const { body } = render(ComposerWithQueryClient, {
      props: { ...base, running: true, supportsSteer: true, heldPromptRows: [held("a", "After that, log each 429")] }
    });
    expect(body).not.toContain("data-conversation-held-stack");
  });
});
