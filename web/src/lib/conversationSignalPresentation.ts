import type { FieldStageVisualState } from "./ui";

export type ConversationSignals = {
  conversation_id: string | null;
  needs_me: boolean;
  agent_working: boolean;
  latest_turn_ended_sequence: number;
};

export type ConversationSignalPresentation = {
  state: FieldStageVisualState;
  ariaLabel: string;
};

/**
 * Present the conversation facts shared by Workspace rows and the Agents roster.
 *
 * A request only the user can answer wins over the running turn that carries it.
 * Otherwise a completed turn is unseen until this browser's per-conversation
 * watermark reaches it.
 */
export function conversationSignalPresentation(
  signals: ConversationSignals,
  replyWatermarks: Readonly<Record<string, number>>
): ConversationSignalPresentation {
  if (signals.needs_me) {
    return { state: "needs-me", ariaLabel: "Needs you" };
  }
  if (signals.agent_working) {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  const latestTurnEnded = Number(signals.latest_turn_ended_sequence ?? 0);
  if (latestTurnEnded === 0 || typeof signals.conversation_id !== "string") {
    return { state: "upcoming", ariaLabel: "Nothing waiting" };
  }
  if (latestTurnEnded > (replyWatermarks[signals.conversation_id] ?? 0)) {
    return { state: "current-awaiting-approval", ariaLabel: "Unseen agent reply" };
  }
  return { state: "reply-seen", ariaLabel: "Agent reply seen" };
}
