import type { FieldStageVisualState } from "./ui";

export type ConversationSignals = {
  conversation_id: string | null;
  needs_me: boolean;
  agent_working: boolean;
  // The position the reader must reach for this row to count as read. Every kind of row
  // supplies where its own conversation last ended a turn. 0 is before every real
  // position, so a row with nothing to show is never lit.
  unread_position: number;
  owner_read_through_sequence: number;
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
  if (signals.needs_me) {
    return { state: "needs-me", ariaLabel: "Needs you" };
  }
  if (signals.agent_working) {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  const unreadPosition = Number(signals.unread_position ?? 0);
  if (unreadPosition === 0 || typeof signals.conversation_id !== "string") {
    return { state: "upcoming", ariaLabel: "Nothing waiting" };
  }
  if (unreadPosition > Number(signals.owner_read_through_sequence ?? 0)) {
    return { state: "current-awaiting-approval", ariaLabel: "Unseen agent reply" };
  }
  return { state: "reply-seen", ariaLabel: "Agent reply seen" };
}
