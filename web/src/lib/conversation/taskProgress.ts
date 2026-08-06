/** The conversation's latest plan and the work that is current inside it.
 *
 * A plan is presentation data for the conversation, not history attached to the turn
 * that first stated it. The newest update replaces the previous plan outright. The
 * running turn is read from the same rows, so every pane state gets one answer about
 * whether progress belongs on the screen.
 */

import type { TranscriptRow } from "./transcript";
import type { PlanEntry } from "./wire";

export type ConversationTaskProgress = {
  entries: readonly PlanEntry[];
  /** The one-based position of the current step. */
  currentPosition: number | null;
  currentEntry: PlanEntry | null;
  completedCount: number;
  hasUnfinishedEntry: boolean;
  turnRunning: boolean;
};

export function taskProgressFrom(
  rows: readonly TranscriptRow[]
): ConversationTaskProgress {
  let entries: readonly PlanEntry[] = [];
  let turnRunning = false;

  for (const row of rows) {
    if (row.kind === "prompt" && !turnRunning) turnRunning = true;
    else if (row.kind === "turn_ended" || row.kind === "turn_stopped") turnRunning = false;
    else if (row.kind === "plan_updated") entries = row.entries;
  }

  const currentIndex = entries.findIndex((entry) => entry.status === "in_progress");
  return {
    entries,
    currentPosition: currentIndex === -1 ? null : currentIndex + 1,
    currentEntry: currentIndex === -1 ? null : entries[currentIndex] ?? null,
    completedCount: entries.filter((entry) => entry.status === "completed").length,
    hasUnfinishedEntry: entries.some((entry) => entry.status !== "completed"),
    turnRunning
  };
}
