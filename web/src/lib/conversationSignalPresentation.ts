import type { FieldStageVisualState } from "./ui";

export type ConversationSignals = {
  awaiting_reply: boolean;
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
  if (signals.awaiting_reply) {
    return { state: "needs-me", ariaLabel: "Message" };
  }
  if (signals.agent_state === "working") {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  if (signals.agent_state === "errored") {
    return { state: "errored", ariaLabel: "Agent errored" };
  }
  return { state: "upcoming", ariaLabel: "Nothing waiting" };
}
