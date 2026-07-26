/** What the composer is allowed to offer right now, worked out away from any markup.
 *
 * Three decisions live here and none of them belongs in a template.
 *
 * **How a message may meet the agent.** Idle, there is nothing to choose: a message runs.
 * Mid-turn there are three ways in, and one of them — steering into the running turn — is
 * a per-backend fact rather than a preference. A backend that cannot steer is not offered
 * steering at all: an option that would always be refused is worse than no option.
 *
 * **What a permission ask offers.** Every ask card has the same three anchors — cancel the
 * turn, decline, approve once — in that order, which is the order of how much they commit
 * to. The backend's own options fill in between. When an ask does not carry an anchor of
 * its own, Panels supplies one and says so, because an ask whose shape nobody understands
 * must still be answerable: an agent that can block invisibly is the one failure that has
 * no recovery.
 *
 * **What a picker changes.** Nothing, until a message is sent. Browsing a model list is
 * browsing; the value rides the next delivery or it never happens at all. A steer cannot
 * carry one — the turn it joins is already running — so the change stays pending.
 */

import type {
  PermissionAskOption,
  PromptDeliveryFate,
  PromptDeliveryMode,
  SendPromptBody
} from "./wire";
import { backendSupportsSteer, type ConversationBackendKey } from "./wire";
import type { OutgoingMessage } from "./outgoing";
import { refusalSentence } from "./transcript";

// --- how a message meets the agent -------------------------------------------------------

export type DeliveryOption = {
  mode: PromptDeliveryMode;
  label: string;
  description: string;
};

const QUEUE_OPTION: DeliveryOption = {
  mode: "run_when_free",
  label: "queue",
  description: "Hold this until the agent is free"
};

const SEND_NOW_OPTION: DeliveryOption = {
  mode: "send_now",
  label: "send now",
  description: "Stop the running turn and run this instead"
};

const STEER_OPTION: DeliveryOption = {
  mode: "steer",
  label: "steer",
  description: "Put this into the turn that is already running"
};

/** The ways in that exist for this backend while a turn is running.
 *
 * Steering is absent rather than greyed out for a backend that cannot do it, because
 * there is nothing here for a person to enable — the backend simply does not take text
 * into a running turn.
 */
export function deliveryOptionsFor(
  backendKey: ConversationBackendKey | null
): DeliveryOption[] {
  const options = [QUEUE_OPTION, SEND_NOW_OPTION];
  if (backendSupportsSteer(backendKey)) options.push(STEER_OPTION);
  return options;
}

export function deliveryModeIsOffered(
  backendKey: ConversationBackendKey | null,
  mode: PromptDeliveryMode
): boolean {
  return deliveryOptionsFor(backendKey).some((option) => option.mode === mode);
}

// --- what an ask offers ------------------------------------------------------------------

export type AskAction =
  /** Always real, always first: it ends the turn the ask belongs to. */
  | { act: "cancel_turn"; label: string; emphasis: "cancel"; supplied: true }
  | {
      act: "answer";
      optionId: string;
      label: string;
      emphasis: "decline" | "option" | "primary";
      /** False when Panels supplied this answer because the ask did not carry one. */
      supplied: boolean;
    };

export const CANCEL_TURN_ACTION: AskAction = {
  act: "cancel_turn",
  label: "Cancel turn",
  emphasis: "cancel",
  supplied: true
};

/** The option ids Panels falls back to. They are the ACP permission kinds, which is the
 * closest thing to a convention an ask that named nothing could have meant. An answer
 * built from one is best-effort and says so, and the server reports whether it landed. */
const FALLBACK_DECLINE_OPTION_ID = "reject_once";
const FALLBACK_APPROVE_OPTION_ID = "allow_once";

function isReject(option: PermissionAskOption): boolean {
  return option.option_kind.startsWith("reject");
}

function isAllowOnce(option: PermissionAskOption): boolean {
  return option.option_kind === "allow_once";
}

/** Every answer this ask offers, in escalating order, with the anchors guaranteed.
 *
 * Cancel turn, then decline, then whatever else the backend offered, then approve once.
 * A missing anchor is supplied rather than left out, so the shape of the card does not
 * depend on the shape of the ask.
 */
export function askActions(
  ask: { options?: readonly PermissionAskOption[] } | null
): AskAction[] {
  const options = ask?.options ?? [];
  const decline = options.find((option) => option.option_kind === "reject_once")
    ?? options.find(isReject);
  const approveOnce = options.find(isAllowOnce);
  const between = options.filter(
    (option) => option !== decline && option !== approveOnce
  );

  const actions: AskAction[] = [CANCEL_TURN_ACTION];
  actions.push({
    act: "answer",
    optionId: decline?.option_id ?? FALLBACK_DECLINE_OPTION_ID,
    label: decline?.label ?? "Decline",
    emphasis: "decline",
    supplied: decline !== undefined
  });
  for (const option of between) {
    actions.push({
      act: "answer",
      optionId: option.option_id,
      label: option.label,
      emphasis: "option",
      supplied: true
    });
  }
  actions.push({
    act: "answer",
    optionId: approveOnce?.option_id ?? FALLBACK_APPROVE_OPTION_ID,
    label: approveOnce?.label ?? "Approve once",
    emphasis: "primary",
    supplied: approveOnce !== undefined
  });
  return actions;
}

/** Whether Panels had to supply an anchor, which is what makes a card the generic one. */
export function askIsGeneric(ask: { options?: readonly PermissionAskOption[] } | null): boolean {
  return askActions(ask).some((action) => !action.supplied);
}

/** The ask's own words, used as the composer's placeholder while it takes over.
 *
 * A placeholder is one line of grey text, so only a detail that is genuinely one short
 * line is used as one. Anything longer — and anything structured — belongs in the card,
 * where it can be read and scrolled; squeezing it in here produced the JSON-in-a-
 * placeholder the owner saw.
 */
const LONGEST_DETAIL_WORTH_A_PLACEHOLDER = 80;

export function askPlaceholder(
  ask: { title: string; detail?: string | null } | null
): string {
  if (ask === null) return "";
  const detail = ask.detail?.trim() ?? "";
  const isOneShortLine =
    detail !== ""
    && !detail.includes("\n")
    && detail.length <= LONGEST_DETAIL_WORTH_A_PLACEHOLDER
    && !detail.startsWith("{")
    && !detail.startsWith("[");
  return isOneShortLine ? detail : ask.title;
}

// --- what a picker changes ---------------------------------------------------------------

export type RunValues = {
  model: string | null;
  reasoningEffort: string | null;
};

/** The change a send would carry, given what the conversation runs on and what was picked.
 *
 * A value equal to the current one is not a change and is not sent. A steer carries no
 * change at all: the turn it joins is already running, and the server treats a steer with
 * a change as a caller's mistake rather than a delivery outcome.
 */
export function armedChangeFor(
  current: RunValues,
  picked: RunValues,
  mode: PromptDeliveryMode
): { model_change?: string; reasoning_effort_change?: string } {
  if (mode === "steer") return {};
  const change: { model_change?: string; reasoning_effort_change?: string } = {};
  if (picked.model !== null && picked.model !== current.model) {
    change.model_change = picked.model;
  }
  if (picked.reasoningEffort !== null && picked.reasoningEffort !== current.reasoningEffort) {
    change.reasoning_effort_change = picked.reasoningEffort;
  }
  return change;
}

export function hasArmedChange(
  current: RunValues,
  picked: RunValues,
  mode: PromptDeliveryMode
): boolean {
  return Object.keys(armedChangeFor(current, picked, mode)).length > 0;
}

/** The whole body of a send, change and all. One place builds it, so one place decides.
 *
 * The message is built from the copy this browser already drew, so what goes out is what
 * is on screen: the same pieces, under the same id, stamped with the same instant.
 */
export function sendBodyFor(input: {
  message: OutgoingMessage;
  current: RunValues;
  picked: RunValues;
}): SendPromptBody {
  return {
    content: input.message.content,
    sender_label: input.message.senderLabel,
    mode: input.message.mode,
    sender_message_id: input.message.messageId,
    sent_at_unix_milliseconds: input.message.sentAtUnixMilliseconds,
    ...armedChangeFor(input.current, input.picked, input.message.mode)
  };
}

/** The name a model value is shown under, from the backend card's own catalog.
 *
 * The wire carries whatever the CLI accepts (claude's bare aliases, codex's slugs);
 * the person reads the catalog's display name for it — "Opus 5", not "opus". A value
 * the catalog does not name is shown as itself rather than dressed up.
 */
export function modelDisplayName(
  models: readonly { model_id: string; display_name: string | null }[],
  value: string | null
): string | null {
  if (value === null) return null;
  const match = models.find((model) => model.model_id === value);
  return match?.display_name ?? value;
}

/** What this model really is, when the catalog says. Absent stays absent. */
export function modelDetail(
  models: readonly { model_id: string; detail?: string | null }[],
  value: string | null
): string | null {
  if (value === null) return null;
  return models.find((model) => model.model_id === value)?.detail ?? null;
}

/** The value a selector shows when nobody has picked anything.
 *
 * "Default" is not a value and is never offered as one — it is a word for whichever
 * concrete value is already in force. So the face shows that concrete value: what the
 * conversation is actually running if the record says, otherwise what the backend runs
 * when nobody names one. A backend that names none leaves the face empty rather than
 * inventing a word, which is the honest end of it.
 */
export function preselectedValue(
  currentValue: string | null,
  backendDefault: string | null | undefined
): string | null {
  return currentValue ?? backendDefault ?? null;
}

/** The reasoning efforts on offer for the model actually in force.
 *
 * Effort is a per-backend list until a backend says otherwise per model — claude's
 * smallest models take none at all. A model that names its own list is believed, including
 * when that list is empty, because "this model takes no effort" is an answer. A model that
 * names nothing falls back to the backend's list, which is where every backend started.
 */
export function effortOptionsFor(
  models: readonly { model_id: string; reasoning_effort_options?: string[] }[],
  modelInForce: string | null,
  backendOptions: readonly string[]
): readonly string[] {
  if (modelInForce === null) return backendOptions;
  const model = models.find((candidate) => candidate.model_id === modelInForce);
  return model?.reasoning_effort_options ?? backendOptions;
}

// --- what shape of ask this is -------------------------------------------------------------

/** The three kinds of thing an agent can stop and wait for.
 *
 * ``permission`` is the ask this pane was built for: the backend offers answers that
 * allow or refuse something. ``question`` is an ask whose options are real choices —
 * answering it is picking one, not approving it, so approving language would be wrong.
 * ``shapeless`` is an ask carrying nothing to pick at all, which must still be escapable.
 */
export type AskShape = "permission" | "question" | "shapeless";

export function askShape(ask: { options?: readonly PermissionAskOption[] } | null): AskShape {
  const options = ask?.options ?? [];
  if (options.length === 0) return "shapeless";
  const offersPermission = options.some(
    (option) => option.option_kind.startsWith("allow") || option.option_kind.startsWith("reject")
  );
  return offersPermission ? "permission" : "question";
}

export type AskQuestionChoice = {
  optionId: string;
  label: string;
  /** The number key that picks this one, for the first nine. Past that there is no key —
   *  the choice is still there and still clickable, it just has no shortcut. */
  shortcutDigit: number | null;
};

export function askQuestionChoices(
  ask: { options?: readonly PermissionAskOption[] } | null
): AskQuestionChoice[] {
  return (ask?.options ?? []).map((option, index) => ({
    optionId: option.option_id,
    label: option.label,
    shortcutDigit: index < 9 ? index + 1 : null
  }));
}

/** The choice a number key picks, or nothing if that key picks none. */
export function askChoiceForDigit(
  ask: { options?: readonly PermissionAskOption[] } | null,
  digit: number
): AskQuestionChoice | null {
  if (!Number.isInteger(digit) || digit < 1 || digit > 9) return null;
  return askQuestionChoices(ask)[digit - 1] ?? null;
}

/** What a delivery's fate says, in the words a person reads under the composer.
 *
 * A started fate says nothing: the turn is already visible as itself — the prompt row,
 * the working header, the stop button — and a note repeating it would outlive the turn
 * and go stale. A refusal is a fate like any other and is reported as one — the text
 * did not get anywhere, and the sentence says why. It is never dressed up as a failed
 * request.
 */
export function fateSentence(fate: PromptDeliveryFate): string | null {
  switch (fate.fate) {
    case "started":
      return null;
    case "queued":
      return `queued · position ${fate.queue_position}`;
    case "injected":
      return "steered into the running turn";
    case "refused":
      return `not delivered · ${refusalSentence(fate.refusal_reason)}`;
  }
}
