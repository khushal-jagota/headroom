/** Preview-only model for in-chat questions and approvals.
 *
 * The live composer is unchanged. These shapes feed the `#/dev/asks` treatments
 * and the keyboard behaviour they share. A number commits a single choice. It
 * only highlights an approval, because a stray digit must not grant one.
 */

import type { PermissionAskOption, UserInputAnswers, UserInputQuestion } from "../conversation/wire";

export type PreviewChoice = {
  id: string;
  label: string;
  description?: string;
};

export type PreviewAction = PreviewChoice & {
  emphasis: "decline" | "option" | "primary";
  optionKind: string;
};

export type PreviewQuestion = {
  id: string;
  header: string;
  prompt: string;
  multi: boolean;
  allowOther: boolean;
  freeText: boolean;
  options: readonly PreviewChoice[];
};

export type PreviewAsk = {
  id: string;
  kind: "question" | "approval";
  /** Which live surface this example uses today. */
  production: "user_input" | "permission";
  stage: string | null;
  detail: string | null;
  questions: readonly PreviewQuestion[];
  actions: readonly PreviewAction[];
};

export type TranscriptLine = {
  role: "agent" | "owner" | "note";
  text: string;
};

export type PreviewScene = {
  id: string;
  label: string;
  lines: readonly TranscriptLine[];
  asks: readonly PreviewAsk[];
};

export type AskDraft = {
  step: number;
  selected: Record<string, string[]>;
  other: Record<string, string>;
  text: Record<string, string>;
};

export type AskVariantId = "current" | "quiet" | "dock" | "composer" | "sheet";

export const ASK_VARIANTS: readonly { id: AskVariantId; label: string; idea: string }[] = [
  {
    id: "current",
    label: "Current",
    idea: "What the composer shows today. Questions are a panel inside the box. Approvals are a band above the buttons."
  },
  {
    id: "quiet",
    label: "Quiet",
    idea: "The ask is the next line of the conversation. Choices are text. The only strong control is the commit."
  },
  {
    id: "dock",
    label: "Dock",
    idea: "One quiet band sits above the composer and holds a single ask. The composer stays a place to write."
  },
  {
    id: "composer",
    label: "Composer",
    idea: "The composer becomes the answer. The question, the choices, and the commit live in the box you already use."
  },
  {
    id: "sheet",
    label: "Sheet",
    idea: "A side plane holds the reading and the decision. The conversation stays put. Several waiting asks are a short list."
  }
];

export function emptyDraft(): AskDraft {
  return { step: 0, selected: {}, other: {}, text: {} };
}

export function questionAt(ask: PreviewAsk, draft: AskDraft): PreviewQuestion | null {
  if (ask.kind !== "question") return null;
  return ask.questions[draft.step] ?? null;
}

export function selectionKey(ask: PreviewAsk, draft: AskDraft): string {
  return ask.kind === "approval" ? ask.id : (questionAt(ask, draft)?.id ?? ask.id);
}

/** Choices the arrow keys move through. A free-text question has none. */
export function cursorChoices(ask: PreviewAsk, draft: AskDraft): readonly PreviewChoice[] {
  if (ask.kind === "approval") return ask.actions;
  const question = questionAt(ask, draft);
  if (question === null || question.freeText) return [];
  return question.options;
}

export function draftWithOther(draft: AskDraft, key: string, value: string, clearSelection: boolean): AskDraft {
  return {
    ...draft,
    selected: clearSelection ? { ...draft.selected, [key]: [] } : draft.selected,
    other: { ...draft.other, [key]: value }
  };
}

export function draftWithText(draft: AskDraft, key: string, value: string): AskDraft {
  return { ...draft, text: { ...draft.text, [key]: value } };
}

export function draftWithToggle(draft: AskDraft, key: string, choiceId: string, multi: boolean): AskDraft {
  const current = draft.selected[key] ?? [];
  const next = multi
    ? current.includes(choiceId)
      ? current.filter((id) => id !== choiceId)
      : [...current, choiceId]
    : [choiceId];
  return {
    ...draft,
    selected: { ...draft.selected, [key]: next },
    other: multi ? draft.other : { ...draft.other, [key]: "" }
  };
}

export function answerReady(ask: PreviewAsk, draft: AskDraft): boolean {
  if (ask.kind === "approval") return (draft.selected[ask.id] ?? []).length === 1;
  const question = questionAt(ask, draft);
  if (question === null) return false;
  if (question.freeText) return (draft.text[question.id] ?? "").trim().length > 0;
  const selected = draft.selected[question.id] ?? [];
  const other = (draft.other[question.id] ?? "").trim();
  return selected.length > 0 || other.length > 0;
}

function bitsForQuestion(question: PreviewQuestion, draft: AskDraft, extraChoiceId?: string): string[] {
  const ids = new Set(draft.selected[question.id] ?? []);
  if (extraChoiceId) ids.add(extraChoiceId);
  const labels = question.options.filter((choice) => ids.has(choice.id)).map((choice) => choice.label);
  const other = (draft.other[question.id] ?? "").trim();
  const text = (draft.text[question.id] ?? "").trim();
  return [...labels, ...(other ? [other] : []), ...(text ? [text] : [])];
}

/** The owner's reply, in the order the questions were asked. */
export function answerSentence(ask: PreviewAsk, draft: AskDraft, choiceId?: string): string {
  if (ask.kind === "approval") {
    const id = choiceId ?? draft.selected[ask.id]?.[0];
    return ask.actions.find((action) => action.id === id)?.label ?? "Answered";
  }
  const parts = ask.questions.map((question, index) => {
    const extra = index === draft.step ? choiceId : undefined;
    return bitsForQuestion(question, draft, extra).join(", ");
  });
  return parts.filter((part) => part !== "").join(" · ");
}

export function userInputAnswers(ask: PreviewAsk, draft: AskDraft): UserInputAnswers {
  const answers: UserInputAnswers = {};
  for (const question of ask.questions) {
    const given = bitsForQuestion(question, draft);
    if (given.length > 0) answers[question.id] = { answers: given };
  }
  return answers;
}

export function sentenceFromSubmitted(ask: PreviewAsk, answers: UserInputAnswers): string {
  return ask.questions
    .map((question) => (answers[question.id]?.answers ?? []).join(", "))
    .filter((part) => part !== "")
    .join(" · ");
}

export function toUserInput(ask: PreviewAsk): { requestId: string; questions: UserInputQuestion[] } | null {
  if (ask.production !== "user_input" || ask.kind !== "question") return null;
  return {
    requestId: ask.id,
    questions: ask.questions.map((question) => ({
      question_id: question.id,
      header: question.header,
      question: question.prompt,
      options: question.options.map((choice) => ({
        label: choice.label,
        description: choice.description ?? ""
      })),
      multi_select: question.multi,
      allow_other: question.allowOther || question.freeText
    }))
  };
}

export function toPermissionAsk(ask: PreviewAsk): {
  askId: string;
  title: string;
  detail: string | null;
  options: PermissionAskOption[];
} | null {
  if (ask.production !== "permission") return null;
  if (ask.kind === "approval") {
    return {
      askId: ask.id,
      title: ask.stage ? `${ask.stage} · ${ask.questions[0]?.prompt ?? "Approve"}` : (ask.questions[0]?.prompt ?? "Approve"),
      detail: ask.detail,
      options: ask.actions.map((action) => ({
        option_id: action.id,
        label: action.label,
        option_kind: action.optionKind
      }))
    };
  }
  const question = ask.questions[0];
  if (question === undefined) return null;
  return {
    askId: ask.id,
    title: question.prompt,
    detail: ask.detail,
    options: question.options.map((choice) => ({
      option_id: choice.id,
      label: choice.label,
      option_kind: "choice"
    }))
  };
}

export type AskKeyName = "up" | "down" | "digit" | "commit" | "cancel" | "toggle";

export function askKeyFromEvent(
  event: { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean },
  typing: boolean
): { name: AskKeyName; digit?: number } | null {
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  if (typing) {
    if (event.key === "Escape") return { name: "cancel" };
    if (event.key === "Enter" && !event.shiftKey) return { name: "commit" };
    return null;
  }
  if (event.key === "ArrowUp") return { name: "up" };
  if (event.key === "ArrowDown") return { name: "down" };
  if (event.key === "Escape") return { name: "cancel" };
  if (event.key === "Enter") return { name: "commit" };
  if (event.key === " ") return { name: "toggle" };
  if (/^[1-9]$/.test(event.key)) return { name: "digit", digit: Number(event.key) };
  return null;
}

export type AskCommand =
  | { type: "none" }
  | { type: "cursor"; cursor: number }
  | { type: "toggle"; choiceId: string; cursor: number }
  | { type: "commit-choice"; choiceId: string }
  | { type: "commit" }
  | { type: "cancel" };

/** What a key should do. The caller applies it, so the preview and the tests share one rule. */
export function commandForAskKey(
  ask: PreviewAsk,
  draft: AskDraft,
  cursor: number,
  key: { name: AskKeyName; digit?: number }
): AskCommand {
  if (key.name === "cancel") return { type: "cancel" };
  const choices = cursorChoices(ask, draft);
  const question = questionAt(ask, draft);
  const multi = question?.multi === true;
  const freeText = question?.freeText === true;

  if (key.name === "commit") {
    if (freeText) return answerReady(ask, draft) ? { type: "commit" } : { type: "none" };
    if (multi) return answerReady(ask, draft) ? { type: "commit" } : { type: "none" };
    if (cursor < 0 || cursor >= choices.length) return { type: "none" };
    return { type: "commit-choice", choiceId: choices[cursor].id };
  }

  if (key.name === "up" || key.name === "down") {
    if (choices.length === 0) return { type: "none" };
    const delta = key.name === "down" ? 1 : -1;
    const start = cursor < 0 ? (delta === 1 ? -1 : 0) : cursor;
    const next = (start + delta + choices.length) % choices.length;
    return { type: "cursor", cursor: next };
  }

  if (key.name === "digit") {
    const index = (key.digit ?? 0) - 1;
    if (index < 0 || index >= choices.length) return { type: "none" };
    const choiceId = choices[index].id;
    if (multi) return { type: "toggle", choiceId, cursor: index };
    if (ask.kind === "approval") return { type: "cursor", cursor: index };
    return { type: "commit-choice", choiceId };
  }

  if (key.name === "toggle") {
    if (!multi || cursor < 0 || cursor >= choices.length) return { type: "none" };
    return { type: "toggle", choiceId: choices[cursor].id, cursor };
  }

  return { type: "none" };
}

/** Short labels sit in a row. Anything with a description stacks. */
export function choicesFitARow(choices: readonly PreviewChoice[]): boolean {
  return choices.length > 1
    && choices.length <= 4
    && choices.every((choice) => choice.description === undefined && choice.label.length <= 24);
}

function question(
  id: string,
  header: string,
  prompt: string,
  options: readonly PreviewChoice[],
  extra: Partial<Pick<PreviewQuestion, "multi" | "allowOther" | "freeText">> = {}
): PreviewQuestion {
  return {
    id,
    header,
    prompt,
    multi: extra.multi === true,
    allowOther: extra.allowOther === true,
    freeText: extra.freeText === true,
    options
  };
}

function askQuestion(
  id: string,
  production: PreviewAsk["production"],
  questions: readonly PreviewQuestion[],
  detail: string | null = null
): PreviewAsk {
  return { id, kind: "question", production, stage: null, detail, questions, actions: [] };
}

function askApproval(
  id: string,
  stage: string,
  prompt: string,
  detail: string,
  actions: readonly PreviewAction[]
): PreviewAsk {
  return {
    id,
    kind: "approval",
    production: "permission",
    stage,
    detail,
    questions: [question(`${id}-q`, stage, prompt, [])],
    actions
  };
}

const approveActions: readonly PreviewAction[] = [
  { id: "reject_once", label: "Decline", emphasis: "decline", optionKind: "reject_once" },
  { id: "allow_always", label: "Allow for this ticket", emphasis: "option", optionKind: "allow_always" },
  { id: "allow_once", label: "Approve", emphasis: "primary", optionKind: "allow_once" }
];

const shortApprove: readonly PreviewAction[] = [
  { id: "reject_once", label: "Decline", emphasis: "decline", optionKind: "reject_once" },
  { id: "allow_once", label: "Approve", emphasis: "primary", optionKind: "allow_once" }
];

const longDetail = `The worker would send this result to the manager and mark the stage settled.

What the result says
The composer still answers questions and approvals the way it does today. Four other treatments sit on a preview route so they can be compared on the same examples. None of them is the default.

What would be true after sending
The manager can read the result without opening the ticket. The worker stops at this stage. A later build can pick up whichever treatment you choose.

What would not happen
The live cards stay. The ceiling picker is not moved. Nothing merges.

The note is long on purpose: the treatment has to stay readable when the agent sends a plan, not a sentence.`;

export const ASK_SCENES: readonly PreviewScene[] = [
  {
    id: "single",
    label: "One choice",
    lines: [
      { role: "agent", text: "The approach is written. I can carry it into the build, stop so you can read it, or hand it back." }
    ],
    asks: [
      askQuestion("single", "user_input", [
        question("timing", "Timing", "How far should this go?", [
          { id: "build", label: "Through the build", description: "Implement the approach and open a pull request." },
          { id: "stop", label: "Stop at the approach", description: "Leave the plan with you. Do not write code." },
          { id: "back", label: "Hand it back", description: "Stop, and ask the manager to revise the approach." }
        ])
      ])
    ]
  },
  {
    id: "steps",
    label: "Two questions",
    lines: [
      { role: "agent", text: "Two things before I continue. The second can wait until the first is answered." }
    ],
    asks: [
      askQuestion("steps", "user_input", [
        question("when", "Timing", "When should this land?", [
          { id: "week", label: "This staging", description: "Open the draft against staging. The live cards stay." },
          { id: "next", label: "Next cut", description: "Hold the draft until the following staging." },
          { id: "later", label: "Not yet", description: "Keep it on the ticket. Do not open a pull request." }
        ]),
        question("who", "Review", "Who reads it before it merges?", [
          { id: "you", label: "You", description: "It waits for you at Review." },
          { id: "manager", label: "The manager", description: "The manager reads it first." },
          { id: "none", label: "No one yet", description: "Leave it on the branch." }
        ])
      ])
    ]
  },
  {
    id: "multi",
    label: "Several",
    lines: [
      { role: "agent", text: "I can cut the brief down. Tell me what has to stay in it." }
    ],
    asks: [
      askQuestion("multi", "user_input", [
        question("keep", "Brief", "What should the brief keep?", [
          { id: "outcome", label: "The outcome", description: "What will be true when the work is done." },
          { id: "scope", label: "What is out of scope", description: "The things the worker must not touch." },
          { id: "files", label: "The files it may touch", description: "A short list, not the whole tree." },
          { id: "proof", label: "How to tell it worked", description: "The check that proves the change." },
          { id: "open", label: "The open questions", description: "What still needs you." }
        ], { multi: true, allowOther: true })
      ])
    ]
  },
  {
    id: "text",
    label: "Typed",
    lines: [
      { role: "agent", text: "The ticket needs a title a person can scan in the rail." }
    ],
    asks: [
      askQuestion("text", "user_input", [
        question("title", "Title", "What should the ticket be called?", [], { freeText: true })
      ])
    ]
  },
  {
    id: "numbered",
    label: "Numbered",
    lines: [
      { role: "agent", text: "Three places could take the first edit. I only need one to start." }
    ],
    asks: [
      askQuestion("numbered", "permission", [
        question("file", "Start", "Which file should I open first?", [
          { id: "panel", label: "The question panel" },
          { id: "band", label: "The approval band" },
          { id: "both", label: "Both, in one pass" }
        ])
      ], "The other files stay as they are until you say otherwise.")
    ]
  },
  {
    id: "approval",
    label: "Approval",
    lines: [
      { role: "agent", text: "The approach is ready. Nothing starts until you answer." }
    ],
    asks: [
      askApproval(
        "approval",
        "Approach",
        "Let the worker start the build?",
        "The worker would edit the conversation composer and add a preview of these treatments. The live cards stay until you pick one. The ceiling picker is left alone.\n\nApproving starts the build on this ticket. Declining stops the turn.",
        approveActions
      )
    ]
  },
  {
    id: "long",
    label: "Long",
    lines: [
      { role: "agent", text: "The result is ready to send. It is the long one, the plan rather than a sentence." }
    ],
    asks: [
      askApproval("long", "Result", "Send this result to the manager?", longDetail, shortApprove)
    ]
  },
  {
    id: "pending",
    label: "Waiting",
    lines: [
      { role: "agent", text: "Three things are waiting. I can take them one at a time." }
    ],
    asks: [
      askQuestion("pending-dock", "user_input", [
        question("dock", "Preview", "Should the preview stay off the dock?", [
          { id: "yes", label: "Yes, keep it unlisted", description: "It opens from its address. The dock does not gain an item." },
          { id: "no", label: "No, list it in settings", description: "Add a quiet link where the other tools live." }
        ])
      ]),
      askApproval(
        "pending-kickoff",
        "Kickoff",
        "Start the worker on this ticket?",
        "Kickoff lets the worker begin. The title and the note stay as they are.",
        shortApprove
      ),
      askQuestion("pending-note", "user_input", [
        question("note", "Note", "Anything the worker should know before it starts?", [], { freeText: true })
      ])
    ]
  }
];

export function sceneById(id: string): PreviewScene {
  return ASK_SCENES.find((scene) => scene.id === id) ?? ASK_SCENES[0];
}

export function variantById(id: string): AskVariantId {
  return ASK_VARIANTS.some((variant) => variant.id === id) ? id as AskVariantId : "current";
}
