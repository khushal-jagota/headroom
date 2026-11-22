import { render } from "svelte/server";
import { describe, expect, it } from "vitest";
import PermissionAskActions from "../src/components/conversation/PermissionAskActions.svelte";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";
import ConversationCredentialRequestCard from "../src/components/conversation/ConversationCredentialRequestCard.svelte";
import TaskProgress from "../src/components/conversation/TaskProgress.svelte";
import { userInputOptionForKey } from "../src/lib/conversation/userInput";
import type { CredentialRequestRowBody } from "../src/lib/conversation/view/contracts";
import type { PlanEntry } from "../src/lib/conversation/wire";

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
    expect(body).toContain("Needs your approval");
    expect(body).toContain(ask.title);
    expect(count(body, "xcrun altool")).toBe(1);
  });

  it("answers with the shared button: cancel first, approve once the one primary, last", () => {
    const { body } = render(PermissionAskActions, {
      props: { ask, onAnswer: () => {}, onCancelTurn: () => {} }
    });
    const order = [...body.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map((match) => match[1]);
    expect(order).toEqual(["cancel_turn", "deny", "always", "allow"]);
    expect(count(body, "cv-button--primary")).toBe(1);
    expect(body).toMatch(/class="cv-button[^"]*cv-button--primary[^"]*"[^>]*data-conversation-ask-action="allow"/);
    expect(body).toMatch(/class="cv-button[^"]*cv-button--secondary[^"]*"[^>]*data-conversation-ask-action="always"/);
    // Deny is a secondary, not a red or ghost button.
    expect(body).toMatch(/class="cv-button[^"]*cv-button--secondary[^"]*"[^>]*data-conversation-ask-action="deny"/);
  });

  it("draws its own answers in the shell's footer when it is given the way out", () => {
    const { body } = render(PermissionAskCard, { props: { ask, onAnswer: () => {}, onCancelTurn: () => {} } });
    const order = [...body.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map((match) => match[1]);
    expect(order).toEqual(["cancel_turn", "deny", "always", "allow"]);
    expect(body).toContain(">Stop turn<");
    // Without it the host draws them, so they are never drawn twice.
    const bare = render(PermissionAskCard, { props: { ask, onAnswer: () => {} } }).body;
    expect(bare).not.toContain("data-conversation-ask-action");
  });
});

describe("the question panel", () => {
  it("says how to answer and uses the shared primary for the next step", () => {
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
    expect(body).toContain("Needs your answer");
    expect(body).toContain("Which illustration direction?");
    // The primary waits, disabled, until there is an answer.
    expect(body).toMatch(/class="cv-button cv-button--primary[^"]*"[^>]*data-user-input-continue[^>]*disabled/);
    expect(body).toContain("Next");
    // Every option carries its number key, and the field for something else is offered.
    expect(body).toMatch(/aria-keyshortcuts="1"/);
    expect(body).toContain("Something else…");
  });

  it("names ending the turn as the permission ask does: Stop turn", () => {
    const { body } = render(UserInputQuestionPanel, {
      props: { request: { requestId: "q-2", questions: [] } }
    });
    expect(body).toMatch(/data-user-input-cancel[^>]*>Stop turn</);
    expect(body).not.toMatch(/>Cancel turn</);
    const actions = render(PermissionAskActions, {
      props: { ask, onAnswer: () => {}, onCancelTurn: () => {} }
    }).body;
    expect(actions).toMatch(/data-conversation-ask-action="cancel_turn"[^>]*>Stop turn</);
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

  it("ends a running turn as the other asks do, first in its row, and only when one runs", () => {
    const running = render(ConversationCredentialRequestCard, { props: { request, onCancelTurn: () => {} } }).body;
    expect(running).toMatch(/data-credential-cancel-turn[^>]*>Stop turn</);
    expect(running.indexOf(">Stop turn<")).toBeLessThan(running.indexOf(">Decline<"));
    const settled = render(ConversationCredentialRequestCard, { props: { request } }).body;
    expect(settled).not.toContain("Stop turn");
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

describe("task progress", () => {
  it("reads as the step in hand, not a count of finished ones", () => {
    const entries = [
      { text: "Reproduce", status: "completed" },
      { text: "Read", status: "completed" },
      { text: "Confirm", status: "in_progress" },
      { text: "Write", status: "pending" },
      { text: "Propose", status: "pending" }
    ] satisfies PlanEntry[];
    const { body } = render(TaskProgress, {
      props: {
        variant: "strip",
        progress: {
          entries,
          currentPosition: 3,
          currentEntry: null,
          completedCount: 2,
          hasUnfinishedEntry: true,
          turnRunning: true
        }
      }
    });
    expect(body).toContain("Task 3 of 5");
    expect(body).toContain("Task 3 of 5: Confirm, 2 complete");
    expect(body).toContain("2 of 5 done");
  });

  it("names the step in hand on the pill, from the indexed excerpt before the list is read", () => {
    const { body } = render(TaskProgress, {
      props: {
        variant: "strip",
        progress: {
          entries: [],
          totalCount: 5,
          currentPosition: 3,
          currentEntry: { text: "Run the sync test under -race on a CI-sized runner", status: "in_progress" },
          completedCount: 2,
          hasUnfinishedEntry: true,
          turnRunning: true
        }
      }
    });
    expect(body).toMatch(/data-conversation-task-current[^>]*>Run the sync test under -race on a CI-sized runner</);
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
