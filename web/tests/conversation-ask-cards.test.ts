import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
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
    expect(body).toContain("data-ask-shield");
    expect(body).toContain(ask.title);
    expect(count(body, "xcrun altool")).toBe(1);
  });

  it("answers with one list of numbered tiles: allow once first and chosen; v9 (2): docked too, Deny is option row 3", () => {
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
    // One tile style for every ask; allow once is chosen by default. v4.1: Deny is labelled in
    // the same tone as the others; its words say it declines.
    expect(body).toMatch(/class="cv-ask-row c2-ask-choice[^"]*is-selected[^"]*"[^>]*data-conversation-ask-action="allow"/);
    expect(count(body, "is-selected")).toBe(1);
    expect(body).toMatch(/<button[^>]*(aria-pressed="true"[^>]*data-conversation-ask-action="allow"|data-conversation-ask-action="allow"[^>]*aria-pressed="true")/);
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
});

describe("the question panel", () => {
  it("says where it is in the questions; standing alone it keeps its own primary, waiting for an answer", () => {
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
    // v4.1: no category label beside the pager.
    expect(body).not.toContain(">Style<");
    expect(body).not.toContain('class="topic');
    expect(body).toContain("Needs your answer");
    expect(body).toContain("Which illustration direction?");
    // v6: option 1 is focused at start (the accent ring) and Next, the white primary, picks
    // it, so Next and the forward arrow are available from the start. Standing alone (not in
    // the composer) the panel draws its own Next.
    expect(body).toMatch(/class="cv-button cv-button--primary[^"]*"[^>]*data-user-input-continue/);
    expect(body).not.toMatch(/data-user-input-continue[^>]*disabled/);
    expect(body).toContain("Next");
    expect(body).not.toMatch(/data-user-input-forward[^>]*disabled/);
    expect(body).toMatch(/class="cv-ask-row is-focused"[^>]*data-user-input-option="Soft shapes"[^>]*data-user-input-option-focused="true"/);
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

  it("keeps the shared primary for questions where several answers may be chosen", () => {
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
    expect(body).toMatch(/class="cv-button cv-button--primary[^"]*"[^>]*data-user-input-continue/);
    expect(body).not.toMatch(/data-user-input-continue[^>]*disabled/);
    expect(body).toContain("Send answer");
    // v6: only the first option carries the focus ring at start.
    expect(count(body, "data-user-input-option-focused")).toBe(1);
    // Its stop row is the same one a permission ask ends in.
    expect(count(body, "data-conversation-ask-stop")).toBe(1);
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
    expect(body).toContain("Rejected");
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

  it("is written in the composer when the panel is docked over it: no second field, a row that says where", () => {
    const { body } = render(UserInputQuestionPanel, {
      props: { request: { requestId: "q-own", questions: [single] }, ownAnswer: "", onSubmit: () => {} }
    });
    // v4: the composer line itself is the place for an own answer: no field and no row here,
    // and no primary of its own (the composer's pill is it).
    expect(body).not.toContain("data-user-input-other");
    expect(body).not.toContain("data-user-input-own");
    expect(body).not.toContain("data-user-input-continue");
    // Standing alone (no composer under it) it keeps its own field.
    const alone = render(UserInputQuestionPanel, { props: { request: { requestId: "q-own", questions: [single] }, onSubmit: () => {} } }).body;
    expect(alone).toContain("data-user-input-other");
  });
});
