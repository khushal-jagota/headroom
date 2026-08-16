import type { FieldStageVisualState } from "./ui";

export type ConversationSignals = {
  conversation_id: string | null;
  needs_me: boolean;
  agent_working: boolean;
  // The position the reader must reach for this row to count as read. Every kind of row
  // supplies where its conversation's last turn ended, which is an ordinary reply.
  // 0 is before every real position, so a row with nothing to show is never lit.
  unread_position: number;
  // A position the conversation reached by asking for the user rather than by replying.
  // A Sprint Item supervisor supplies where it last pinged; nothing else has one, so
  // every other row omits it and reads 0. It lights the same mark as `needs_me`,
  // because it means the same thing: only the user can answer this.
  needs_me_position?: number;
};

export type ConversationSignalPresentation = {
  state: FieldStageVisualState;
  ariaLabel: string;
};

/**
 * Present the conversation facts shared by Workspace rows and the Agents roster.
 *
 * A request only the user can answer wins over the running turn that carries it. That
 * request is either live — a pending ask — or a position the conversation left behind,
 * and an unseen one of either kind takes the same mark.
 *
 * Below that, the row is unseen until this browser's per-conversation watermark reaches
 * the reply position. Both positions belong to one conversation and are read against the
 * one watermark, so reading the conversation clears both.
 */
export function conversationSignalPresentation(
  signals: ConversationSignals,
  replyWatermarks: Readonly<Record<string, number>>
): ConversationSignalPresentation {
  // A row with no conversation has no positions to compare, so nothing it carries is
  // unseen. 0 is before every real position and is never unseen either.
  const watermark =
    typeof signals.conversation_id === "string"
      ? (replyWatermarks[signals.conversation_id] ?? 0)
      : null;
  const unseen = (position: number): boolean =>
    watermark !== null && position > 0 && position > watermark;

  if (signals.needs_me || unseen(Number(signals.needs_me_position ?? 0))) {
    return { state: "needs-me", ariaLabel: "Needs you" };
  }
  if (signals.agent_working) {
    return { state: "current-running", ariaLabel: "Agent working" };
  }
  const unreadPosition = Number(signals.unread_position ?? 0);
  if (unreadPosition === 0 || watermark === null) {
    return { state: "upcoming", ariaLabel: "Nothing waiting" };
  }
  if (unreadPosition > watermark) {
    return { state: "current-awaiting-approval", ariaLabel: "Unseen agent reply" };
  }
  return { state: "reply-seen", ariaLabel: "Agent reply seen" };
}
