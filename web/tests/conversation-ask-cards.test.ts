import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";
import ConversationCredentialRequestCard from "../src/components/conversation/ConversationCredentialRequestCard.svelte";
import { userInputOptionForKey } from "../src/lib/conversation/userInput";
import type { CredentialRequestRowBody } from "../src/lib/conversation/view/contracts";

const command = "xcrun altool --upload-app -f build/Wren-41.ipa -t ios --apiKey $ASC_KEY_ID";
const ask = {
  askId: "ask-1",
  title: "Upload Wren build 41 to TestFlight",
  detail: command,
  options: [
    { option_id: "deny", label: "Deny", option_kind: "reject_once" },
    { option_id: "always", label: "Always allow uploads", option_kind: "allow_always" },
    { option_id: "allow", label: "Allow once", option_kind: "allow_once" }
  ]
};

function count(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1;
}

describe("the permission ask", () => {
  it("names what it needs and shows the command once", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } });
    expect(body).toContain("Needs your permission");
    // The same accent dot as a question and a proposal, no shield.
    expect(body).toContain("cv-ask-dot");
    expect(body).not.toContain("data-ask-shield");
    expect(body).toContain(ask.title);
    // Each word of the command is its own piece; the text reads once.
    expect(count(body.replace(/<[^>]+>/g, ""), "xcrun altool")).toBe(1);
  });

  it("answers with one list of numbered tiles, allow once first, none pre-chosen; Deny is option row 3, docked too", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {}, docked: true } });
    const order = [...body.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map((match) => match[1]);
    expect(order).toEqual(["allow", "always", "deny"]);
    expect(body).toMatch(/data-conversation-ask-action="deny"[^>]*aria-keyshortcuts="3"/);
    // Standing alone (not docked), every answer, Deny too, is a tile.
    const alone = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } }).body;
    expect([...alone.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map((match) => match[1])).toEqual(["allow", "always", "deny"]);
    expect(alone).toMatch(/data-conversation-ask-action="deny"[^>]*aria-keyshortcuts="3"/);
    // No row of big buttons on the composer: rows, each with its number key.
    expect(body).not.toContain("cv-button--primary");
    expect(body).not.toContain("cv-button--outline");
    // One tile style for every ask. No row is pre-highlighted (each row is the action),
    // and Deny is labelled in the same tone as the others.
    expect(count(body, "is-selected")).toBe(0);
    expect(body).not.toContain("aria-pressed");
    expect(count(body, "is-quiet")).toBe(0);
    expect(body).toMatch(/data-conversation-ask-action="allow"[^>]*aria-keyshortcuts="1"/);
    // The command is read before the answers.
    expect(body.indexOf("xcrun altool")).toBeLessThan(body.indexOf('data-conversation-ask-action="allow"'));
  });

  it("draws no way to stop the turn when it is not given one", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } });
    expect(body).not.toContain("cancel_turn");
    expect(body).not.toContain("data-conversation-stop");
  });

  it("offers Stop turn as a small button (stop square + label) in its header when the waiting turn can be stopped", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {}, onStop: () => {} } });
    expect(count(body, "data-conversation-stop")).toBe(1);
    expect(body).toMatch(/<button type="button" class="cv-ask-stop[^"]*"[^>]*data-conversation-stop[^>]*><svg class="cv-ask-stop-mark[^"]*" viewBox="0 0 16 16"[^>]*><rect[^>]*><\/rect><\/svg>Stop turn<\/button>/);
    // In the header row, before the question and the answers.
    expect(body.indexOf("data-conversation-ask-stop")).toBeLessThan(body.indexOf(ask.title));
    expect(body.indexOf("data-conversation-ask-stop")).toBeLessThan(body.indexOf('data-conversation-ask-action="allow"'));
  });

  it("still answers an ask that offered nothing, with the answers Panels supplies", () => {
    const { body } = render(PermissionAskCard, { props: { ask: { ...ask, options: [] }, onAnswer: () => {} } });
    const order = [...body.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map((match) => match[1]);
    expect(order).toEqual(["allow_once", "reject_once"]);
    expect(body).toContain('data-conversation-ask-supplied="false"');
  });
  it("wraps a command only at spaces: every word is one piece", () => {
    const upload = { ...ask, detail: "xcrun altool --upload-app -f build/Wren-41.ipa -t ios" };
    const { body } = render(PermissionAskCard, { props: { ask: upload, onAnswer: () => {} } });
    const words = [...body.matchAll(/<span class="c2-ask-word[^"]*">([^<]*)<\/span>/g)].map((match) => match[1]);
    expect(words).toEqual(["xcrun", "altool", "--upload-app", "-f", "build/Wren-41.ipa", "-t", "ios"]);
  });

  it("gives only the first answer the lead, and Deny its decline tone", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } });
    const emphases = [...body.matchAll(/data-conversation-ask-emphasis="(\w+)"/g)].map((match) => match[1]);
    expect(emphases).toEqual(["primary", "option", "decline"]);
    expect(body.indexOf('data-conversation-ask-emphasis="primary"')).toBeLessThan(body.indexOf("Allow once"));
  });
});

describe("the question panel", () => {
  it("says where it is in the questions, with no confirm pill and no pre-highlighted row", () => {
    const { body } = render(UserInputQuestionPanel, {
      props: {
        request: {
          requestId: "q-1",
          questions: [
            {
              question_id: "style",
              header: "Style",
              question: "Which illustration direction?",
              multi_select: false,
              allow_other: true,
              options: [{ label: "Soft shapes", description: "Rounded forms." }]
            },
            {
              question_id: "tone",
              header: "Tone",
              question: "Which tone?",
              multi_select: true,
              allow_other: false,
              options: [{ label: "Warm", description: "" }]
            }
          ]
        }
      }
    });
    expect(body).toContain("1 of 2");
    // No category label beside the pager.
    expect(body).not.toContain(">Style<");
    expect(body).not.toContain('class="topic');
    expect(body).toContain("Needs your answer");
    expect(body).toContain("Which illustration direction?");
    // No confirm pill, standing alone or docked; no row is pre-highlighted. Back is
    // unavailable on the first question; forward moves on (unavailable on the last).
    expect(body).not.toContain("data-user-input-continue");
    expect(body).not.toContain("cv-button--primary");
    expect(body).toMatch(/data-user-input-back[^>]*disabled/);
    expect(body).not.toMatch(/data-user-input-forward[^>]*disabled/);
    expect(body).not.toContain("is-focused");
    expect(body).not.toContain("is-selected");
    // The options are the one ask row, the same as a permission ask's answers.
    expect(body).toMatch(/class="cv-ask-row[^"]*"[^>]*data-user-input-option="Soft shapes"/);
    // Every option carries its number key, and the field for something else is offered.
    expect(body).toMatch(/aria-keyshortcuts="1"/);
    expect(body).toContain("Something else…");
  });

  it("draws no stop of its own when it is not given one", () => {
    const { body } = render(UserInputQuestionPanel, {
      props: { request: { requestId: "q-2", questions: [] } }
    });
    expect(body).not.toContain("data-user-input-cancel");
    expect(body).not.toContain("data-conversation-stop");
  });

  it("draws no pill for a several-answer question: rows toggle and the composer sends", () => {
    const { body } = render(UserInputQuestionPanel, {
      props: {
        onStop: () => {},
        request: {
          requestId: "q-multi",
          questions: [{ question_id: "tone", header: "Tone", question: "Which tones?", multi_select: true, allow_other: false,
            options: [{ label: "Warm", description: "" }, { label: "Plain", description: "" }] }]
        }
      }
    });
    // A several-answer question has no pill of its own either: rows toggle, and the
    // composer's send goes on. Nothing is pre-highlighted.
    expect(body).not.toContain("data-user-input-continue");
    expect(body).not.toContain("is-focused");
    // Its stop row is the same one a permission ask ends in.
    expect(count(body, "data-conversation-ask-stop")).toBe(1);
  });
  it("moves on when a single-answer row is chosen, back to a passed-over question when the last cannot send yet", () => {
    // Choosing is a click and the tests run without a DOM; the wiring is read from the source.
    const panel = readFileSync(new URL("../src/components/conversation/UserInputQuestionPanel.svelte", import.meta.url), "utf8") as string;
    expect(panel).toMatch(/if \(question\.multi_select\) \{\s*draft = toggleUserInputChoice\(draft, question, option\);\s*return;\s*\}[\s\S]*?continueOrSubmit\(\);\s*\}/);
    expect(panel).toContain("if (open >= 0) selectStep(open);");
  });
});

describe("the credential request", () => {
  const request: CredentialRequestRowBody = {
    kind: "credential_request",
    request_id: "r-1",
    name: "SENTRY_AUTH_TOKEN",
    provider: "Sentry",
    use: "Upload dSYMs from CI after each TestFlight build.",
    auth_kind: "bearer",
    header_name: null,
    help: "Sentry → Settings → Auth Tokens.",
    help_url: "https://docs.sentry.io/account/auth-tokens/",
    replacement: null,
    state: "pending",
    credential: null,
    delivery: null
  };

  it("has a title saying what the key is for, labelled fields and a worded help toggle", () => {
    const { body } = render(ConversationCredentialRequestCard, { props: { request } });
    expect(body).toContain("Sentry API key");
    expect(body).toContain(request.use);
    expect(body).toContain("Key name");
    expect(body).toContain("How to get a key");
    expect(body).not.toMatch(/>\?<\/button>/);
    expect(body).toMatch(/class="cv-button cv-button--primary[^"]*"[^>]*>Save key</);
  });

  it("wears the ask shell: an accent label, and the one primary last in its action row", () => {
    const { body } = render(ConversationCredentialRequestCard, { props: { request } });
    expect(body).toContain("Needs a key");
    expect(body).toMatch(/class="key-request[^"]*is-ask/);
    expect(body).toContain('data-ask-kind="credential"');
    expect(body.indexOf(">Decline<")).toBeGreaterThan(-1);
    expect(body.indexOf(">Decline<")).toBeLessThan(body.indexOf(">Save key<"));
  });

  it("holds Save until there is a key, as a question holds Next until there is an answer", () => {
    const { body } = render(ConversationCredentialRequestCard, { props: { request } });
    expect(body).toMatch(/<button[^>]*class="cv-button cv-button--primary[^"]*"[^>]*data-credential-save[^>]*disabled[^>]*>Save key</);
  });

  it("offers the help as a link-styled disclosure", () => {
    const { body } = render(ConversationCredentialRequestCard, { props: { request } });
    expect(body).toMatch(/class="help-toggle[^"]*"[^>]*aria-expanded="false"[^>]*>How to get a key<svg class="help-chevron/);
  });

  it("keeps a settled request a quiet line, without the ask label", () => {
    const { body } = render(ConversationCredentialRequestCard, {
      props: { request: { ...request, state: "rejected" } }
    });
    expect(body).not.toContain("Needs a key");
    expect(body).not.toMatch(/class="key-request[^"]*is-ask/);
    // The shared system line with its hanging dot, in the Decline button's word.
    expect(body.replace(/<!--[^]*?-->/g, "")).toMatch(/<div class="cv-system-line[^"]*is-hanging[^"]*"[^>]*data-credential-record[^]*>Sentry API key · <span class="saved-name[^"]*">SENTRY_AUTH_TOKEN<\/span> · Declined</);
    expect(body).not.toContain("Rejected");
  });
});

describe("answering a question from the keyboard", () => {
  it("picks the option a number key names, and nothing for any other key", () => {
    const question = { options: [{ label: "Soft shapes", description: "" }, { label: "Line art", description: "" }] };
    expect(userInputOptionForKey(question, "1")).toBe("Soft shapes");
    expect(userInputOptionForKey(question, "2")).toBe("Line art");
    expect(userInputOptionForKey(question, "3")).toBeNull();
    expect(userInputOptionForKey(question, "0")).toBeNull();
    expect(userInputOptionForKey(question, "a")).toBeNull();
    expect(userInputOptionForKey(undefined, "1")).toBeNull();
  });
});

describe("the ask shell", () => {
  it("marks its root so the composer's box gives up its own plane, and draws no reply strip it cannot wire", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {}, onCancelTurn: () => {} } });
    expect(body).toContain("data-ask-shell");
    expect(body).toContain('data-ask-kind="permission"');
    expect(body).not.toContain("cv-ask-reply");
  });
});

describe("an own answer written in the composer", () => {
  const single = { question_id: "style", header: "Style", question: "Which?", multi_select: false, allow_other: true, options: [{ label: "Soft shapes", description: "" }] };
  const multi = { ...single, question_id: "tone", multi_select: true };

  it("replaces a chosen option on a single-answer question, and is the answer sent", async () => {
    const { withOwnAnswer, toggleUserInputChoice, emptyUserInputDraft, completeUserInputAnswers } = await import("../src/lib/conversation/userInput");
    const chosen = toggleUserInputChoice(emptyUserInputDraft(), single, "Soft shapes");
    const own = withOwnAnswer(chosen, single, "Hand-drawn ink");
    expect(own.selected.style).toEqual([]);
    expect(completeUserInputAnswers(own, [single])).toEqual({ style: { answers: ["Hand-drawn ink"] } });
    // Clearing the words takes the own answer back and leaves nothing chosen.
    expect(completeUserInputAnswers(withOwnAnswer(own, single, ""), [single])).toBeNull();
  });

  it("joins the chosen options on a multi-answer question", async () => {
    const { withOwnAnswer, toggleUserInputChoice, emptyUserInputDraft, completeUserInputAnswers } = await import("../src/lib/conversation/userInput");
    const chosen = toggleUserInputChoice(emptyUserInputDraft(), multi, "Soft shapes");
    expect(completeUserInputAnswers(withOwnAnswer(chosen, multi, "Warm"), [multi])).toEqual({ tone: { answers: ["Soft shapes", "Warm"] } });
  });

  it("keeps attachment-only answers with their question and refuses files for an exact-choice question", async () => {
    const { completeUserInputAnswers, emptyUserInputDraft, toggleUserInputChoice } = await import("../src/lib/conversation/userInput");
    const file = { piece: "file" as const, data: "bm90ZXM=", file_name: "notes.txt", media_type: "text/plain" };
    const second = { ...single, question_id: "proof" };
    const selected = toggleUserInputChoice(emptyUserInputDraft(), second, "Soft shapes");
    expect(completeUserInputAnswers(selected, [single, second], { style: [file] })).toEqual({
      style: { answers: [], attachments: [file] }, proof: { answers: ["Soft shapes"] }
    });
    expect(completeUserInputAnswers(selected, [{ ...single, allow_other: false }, second], { style: [file] })).toBeNull();
    expect(completeUserInputAnswers(selected, [single, second])).toBeNull();
  });

  it("is written in the composer when the panel is docked over it: no second field, a row that says where", () => {
    const { body } = render(UserInputQuestionPanel, {
      props: { request: { requestId: "q-own", questions: [single] }, ownAnswer: "", onSubmit: () => {} }
    });
    // The composer line itself is the place for an own answer: no field and no row here,
    // and no primary of its own (the composer's pill is it).
    expect(body).not.toContain("data-user-input-other");
    expect(body).not.toContain("data-user-input-own");
    expect(body).not.toContain("data-user-input-continue");
    // Standing alone (no composer under it) it keeps its own field.
    const alone = render(UserInputQuestionPanel, { props: { request: { requestId: "q-own", questions: [single] }, onSubmit: () => {} } }).body;
    expect(alone).toContain("data-user-input-other");
  });
});
