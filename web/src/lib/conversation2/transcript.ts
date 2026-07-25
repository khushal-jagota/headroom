/** The rows a reader holds, turned into the lines a person reads.
 *
 * A conversation's record is a numbered run of finished things, and most of them are a
 * line on their own. Two are not: a tool call's finish belongs to the line its start
 * already drew, and an answer belongs to the ask it answered. Folding those two keeps the
 * thread chronological — each line sits where it began — while still showing how it went.
 *
 * The one judgement in here is about asks. An ask that was never answered and whose turn
 * has since ended is not waiting for anybody: it died with its turn. It is drawn plainly
 * dead rather than left looking live, because an ask that still offers buttons nobody can
 * land is worse than no ask at all.
 */

import type {
  ConversationTurnEnding,
  PermissionAskOption,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason
} from "./wire";
import type { ConversationFeed } from "./feed";

export type PermissionAskState = "live" | "answered" | "dead";

/** Why a dead ask is dead. Both are the same death — its turn is gone — but they are not
 *  the same story, and a reader who was told the wrong one would go looking for a turn
 *  ending that was never written. */
export type PermissionAskDeadReason = "turn_ended" | "no_ending_recorded";

export type TranscriptRow =
  | {
      key: string;
      kind: "prompt";
      sequence: number;
      text: string;
      senderLabel: string;
      mode: PromptDeliveryMode;
    }
  | {
      key: string;
      kind: "prompt_refused";
      sequence: number;
      text: string;
      senderLabel: string;
      reason: PromptDeliveryRefusalReason;
      sentence: string;
    }
  | {
      key: string;
      kind: "prompt_discarded";
      sequence: number;
      text: string;
      senderLabel: string;
    }
  | { key: string; kind: "agent_message"; sequence: number; text: string }
  | {
      key: string;
      kind: "tool_call";
      sequence: number;
      toolCallId: string;
      title: string;
      toolKind: string;
      detail: string | null;
      status: "running" | "completed" | "failed";
      progress: string | null;
    }
  | {
      key: string;
      kind: "permission_ask";
      sequence: number;
      askId: string;
      title: string;
      detail: string | null;
      options: readonly PermissionAskOption[];
      state: PermissionAskState;
      deadReason: PermissionAskDeadReason | null;
      answeredOptionLabel: string | null;
    }
  | {
      key: string;
      kind: "model_changed";
      sequence: number;
      model: string | null;
      reasoningEffort: string | null;
    }
  | {
      key: string;
      kind: "turn_ended";
      sequence: number;
      ending: ConversationTurnEnding;
      errorSummary: string | null;
    }
  /** Not a row: the pane saying that the turn the last rows left open is not running any
   *  more, and that no ending was ever written for it. Without this the thread would just
   *  stop, which reads as a turn still going. */
  | { key: string; kind: "turn_stopped"; sequence: number }
  /** Agent text that is still arriving. Replaced by its row, never kept beside it. */
  | { key: string; kind: "streaming_agent_message"; sequence: number; text: string };

const REFUSAL_SENTENCES: Record<PromptDeliveryRefusalReason, string> = {
  no_such_conversation: "there is no such conversation to send to",
  backend_did_not_start: "the backend would not start",
  session_did_not_load: "the backend's session would not load",
  write_to_backend_failed: "the write to the backend did not succeed",
  no_running_turn_to_steer_into: "there was no running turn to steer into",
  backend_cannot_steer: "this backend cannot take text into a running turn"
};

export function refusalSentence(reason: PromptDeliveryRefusalReason): string {
  return REFUSAL_SENTENCES[reason] ?? "the delivery was impossible";
}

const TURN_ENDING_SENTENCES: Record<ConversationTurnEnding, string> = {
  completed: "turn complete",
  failed: "turn failed",
  interrupted: "turn interrupted"
};

export function turnEndingSentence(
  ending: ConversationTurnEnding,
  errorSummary: string | null
): string {
  const base = TURN_ENDING_SENTENCES[ending] ?? "turn ended";
  return errorSummary ? `${base} · ${errorSummary}` : base;
}

export type TranscriptReading = {
  /** The conversation system says no turn is running while the rows still leave one
   *  open. The turn stopped without an ending, so the pane says the ending the record
   *  will never contain, and the asks that were waiting on it are dead. */
  turnStoppedWithoutAnEnding?: boolean;
};

const ASK_DEAD_SENTENCES: Record<PermissionAskDeadReason, string> = {
  turn_ended: "expired with the turn",
  no_ending_recorded: "expired — its turn stopped without an ending"
};

/** Why this ask can no longer be answered, in the words a person reads.
 *
 * The second one does not name a cause it cannot prove. What is known is that the turn
 * is not running and no ending was ever written for it — a server that went away mid-turn
 * leaves exactly that, and so does anything else that stops a process — so that, and not
 * a guess about which, is what it says.
 */
export function askDeadSentence(reason: PermissionAskDeadReason | null): string {
  return reason === null ? "expired with the turn" : ASK_DEAD_SENTENCES[reason];
}

/** The pane's own words for a turn whose ending the record will never contain. */
export const TURN_STOPPED_SENTENCE = "turn stopped without an ending";

function killOpenAsks(
  rows: TranscriptRow[],
  askRowIndex: Map<string, number>,
  reason: PermissionAskDeadReason
): void {
  for (const at of askRowIndex.values()) {
    const asked = rows[at];
    if (asked?.kind === "permission_ask" && asked.state === "live") {
      rows[at] = { ...asked, state: "dead", deadReason: reason };
    }
  }
  askRowIndex.clear();
}

export function transcriptRows(
  feed: ConversationFeed,
  reading: TranscriptReading = {}
): TranscriptRow[] {
  const rows: TranscriptRow[] = [];
  const toolCallRowIndex = new Map<string, number>();
  const askRowIndex = new Map<string, number>();
  // Half-finished output belongs to a turn that is running. When the turn is gone,
  // whatever was left in flight is not arriving, and it is not drawn.
  const turnIsGone = reading.turnStoppedWithoutAnEnding === true;

  for (const event of feed.events) {
    const sequence = event.sequence;
    switch (event.kind) {
      case "prompt":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt",
          sequence,
          text: event.payload.text,
          senderLabel: event.payload.sender_label,
          mode: event.payload.mode
        });
        break;
      case "prompt_delivery_refused":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt_refused",
          sequence,
          text: event.payload.text,
          senderLabel: event.payload.sender_label,
          reason: event.payload.refusal_reason,
          sentence: refusalSentence(event.payload.refusal_reason)
        });
        break;
      case "prompt_discarded":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt_discarded",
          sequence,
          text: event.payload.text,
          senderLabel: event.payload.sender_label
        });
        break;
      case "agent_message":
        rows.push({
          key: `e${sequence}`,
          kind: "agent_message",
          sequence,
          text: event.payload.text
        });
        break;
      case "tool_call_started":
        toolCallRowIndex.set(event.payload.tool_call_id, rows.length);
        rows.push({
          key: `e${sequence}`,
          kind: "tool_call",
          sequence,
          toolCallId: event.payload.tool_call_id,
          title: event.payload.title,
          toolKind: event.payload.tool_kind,
          detail: event.payload.detail,
          status: "running",
          progress: null
        });
        break;
      case "tool_call_finished": {
        const at = toolCallRowIndex.get(event.payload.tool_call_id);
        const started = at === undefined ? undefined : rows[at];
        if (at !== undefined && started?.kind === "tool_call") {
          rows[at] = {
            ...started,
            status: event.payload.tool_call_status,
            detail: event.payload.detail ?? started.detail,
            progress: null
          };
          break;
        }
        // A finish with no start held is still a line: the record says it happened.
        rows.push({
          key: `e${sequence}`,
          kind: "tool_call",
          sequence,
          toolCallId: event.payload.tool_call_id,
          title: event.payload.tool_call_id,
          toolKind: "other",
          detail: event.payload.detail,
          status: event.payload.tool_call_status,
          progress: null
        });
        break;
      }
      case "permission_asked":
        askRowIndex.set(event.payload.ask_id, rows.length);
        rows.push({
          key: `e${sequence}`,
          kind: "permission_ask",
          sequence,
          askId: event.payload.ask_id,
          title: event.payload.title,
          detail: event.payload.detail,
          options: event.payload.options,
          state: "live",
          deadReason: null,
          answeredOptionLabel: null
        });
        break;
      case "permission_answered": {
        const at = askRowIndex.get(event.payload.ask_id);
        const asked = at === undefined ? undefined : rows[at];
        if (at !== undefined && asked?.kind === "permission_ask") {
          const chosen = asked.options.find(
            (option) => option.option_id === event.payload.option_id
          );
          rows[at] = {
            ...asked,
            state: "answered",
            answeredOptionLabel: chosen?.label ?? event.payload.option_id
          };
        }
        break;
      }
      case "model_changed":
        rows.push({
          key: `e${sequence}`,
          kind: "model_changed",
          sequence,
          model: event.payload.model,
          reasoningEffort: event.payload.reasoning_effort
        });
        break;
      case "turn_ended":
        // Every ask still open belonged to the turn that just ended, so it ended too.
        killOpenAsks(rows, askRowIndex, "turn_ended");
        rows.push({
          key: `e${sequence}`,
          kind: "turn_ended",
          sequence,
          ending: event.payload.ending,
          errorSummary: event.payload.error_summary
        });
        break;
    }
  }

  if (turnIsGone) {
    // The turn is gone and its ending was never written, so the asks that were waiting
    // on it are as dead as any other — they just have a different story.
    killOpenAsks(rows, askRowIndex, "no_ending_recorded");
    rows.push({
      key: "turn-stopped",
      kind: "turn_stopped",
      sequence: feed.latestSequence + 1
    });
  }

  for (const [toolCallId, at] of toolCallRowIndex) {
    const progress = feed.toolCallProgress[toolCallId];
    const row = rows[at];
    if (!turnIsGone && progress !== undefined && row?.kind === "tool_call" && row.status === "running") {
      rows[at] = { ...row, progress };
    }
  }

  if (feed.streamingAgentText !== "" && !turnIsGone) {
    rows.push({
      key: "streaming",
      kind: "streaming_agent_message",
      sequence: feed.latestSequence + 1,
      text: feed.streamingAgentText
    });
  }

  return rows;
}

/** The ask that is waiting for a person right now, as the rows tell it.
 *
 * The snapshot answers this at the moment it was fetched. The rows answer it now, which
 * is what a pane following a live tail needs: an ask that has just been answered, or
 * whose turn has just ended, stops taking over the composer without another fetch.
 */
export function liveAskFrom(rows: readonly TranscriptRow[]): Extract<
  TranscriptRow,
  { kind: "permission_ask" }
> | null {
  for (let at = rows.length - 1; at >= 0; at -= 1) {
    const row = rows[at];
    if (row?.kind === "permission_ask" && row.state === "live") return row;
  }
  return null;
}
