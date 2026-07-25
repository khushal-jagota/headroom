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

/** The ask's own words, used as the composer's placeholder while it takes over. */
export function askPlaceholder(
  ask: { title: string; detail?: string | null } | null
): string {
  if (ask === null) return "";
  return ask.detail && ask.detail !== "" ? ask.detail : ask.title;
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

/** The whole body of a send, change and all. One place builds it, so one place decides. */
export function sendBodyFor(input: {
  text: string;
  senderLabel: string;
  mode: PromptDeliveryMode;
  current: RunValues;
  picked: RunValues;
}): SendPromptBody {
  return {
    text: input.text,
    sender_label: input.senderLabel,
    mode: input.mode,
    ...armedChangeFor(input.current, input.picked, input.mode)
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
