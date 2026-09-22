import type { FieldStageVisualState } from "./ui";

export type ConversationSignals = {
  awaiting_reply: boolean;
  awaiting_answer?: boolean;
  agent_state: "working" | "idle" | "errored";
};

export type ConversationSignalPresentation = {
  state: FieldStageVisualState;
  ariaLabel: string;
};

/**
 * Present the conversation facts shared by Workspace rows and the Agents roster.
 *
 * A request only the user can answer wins over the running turn that carries it.
 * Otherwise the row is unseen until the owner's server-side read position reaches the
 * position the caller supplied.
 */
export function conversationSignalPresentation(
  signals: ConversationSignals
): ConversationSignalPresentation {
  // The pure white dot is the worker waiting on an answer only the owner can give,
  // which is what `needs-me` has always said it meant. A message is a different fact
  // and takes the mark the rail already gave it.
  if (signals.awaiting_answer) {
    return { state: "needs-me", ariaLabel: "Needs an answer" };
  }
  if (signals.awaiting_reply) {
    return { state: "current-awaiting-approval", ariaLabel: "Message" };
  }
  if (signals.agent_state === "working") {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  if (signals.agent_state === "errored") {
    return { state: "errored", ariaLabel: "Agent errored" };
  }
  return { state: "upcoming", ariaLabel: "Nothing waiting" };
}
