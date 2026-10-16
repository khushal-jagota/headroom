import type { FieldStageVisualState } from "./ui";

export type ConversationSignals = {
  awaiting_reply: boolean;
  awaiting_answer?: boolean;
  agent_state?: "working" | "idle" | "errored";
};

export type ConversationSignalPresentation = {
  state: FieldStageVisualState;
  ariaLabel: string;
};

/** A Worker dot belongs only to that exact conversation's unread owner message. */
export function conversationSignalPresentation(
  signals: ConversationSignals
): ConversationSignalPresentation | null {
  return signals.awaiting_reply
    ? { state: "current-awaiting-approval", ariaLabel: "Unread reply" }
    : null;
}
