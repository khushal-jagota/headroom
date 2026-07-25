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
      createdAt: number;
      text: string;
      senderLabel: string;
      mode: PromptDeliveryMode;
    }
  | {
      key: string;
      kind: "prompt_refused";
      sequence: number;
      createdAt: number;
      text: string;
      senderLabel: string;
      reason: PromptDeliveryRefusalReason;
      sentence: string;
    }
  | {
      key: string;
      kind: "prompt_discarded";
      sequence: number;
      createdAt: number;
      text: string;
      senderLabel: string;
    }
  | { key: string; kind: "agent_message"; sequence: number; createdAt: number; text: string }
  | {
      key: string;
      kind: "tool_call";
      sequence: number;
      createdAt: number;
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
      createdAt: number;
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
      createdAt: number;
      model: string | null;
      reasoningEffort: string | null;
    }
  | {
      key: string;
      kind: "turn_ended";
      sequence: number;
      createdAt: number;
      ending: ConversationTurnEnding;
      errorSummary: string | null;
    }
  /** Not a row: the pane saying that the turn the last rows left open is not running any
   *  more, and that no ending was ever written for it. Without this the thread would just
   *  stop, which reads as a turn still going. */
  | { key: string; kind: "turn_stopped"; sequence: number; createdAt: number }
  /** Agent text that is still arriving. Replaced by its row, never kept beside it. */
  | { key: string; kind: "streaming_agent_message"; sequence: number; createdAt: number; text: string };

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
    const createdAt = event.created_at;
    switch (event.kind) {
      case "prompt":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt",
          sequence,
          createdAt,
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
          createdAt,
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
          createdAt,
          text: event.payload.text,
          senderLabel: event.payload.sender_label
        });
        break;
      case "agent_message":
        rows.push({
          key: `e${sequence}`,
          kind: "agent_message",
          sequence,
          createdAt,
          text: event.payload.text
        });
        break;
      case "tool_call_started":
        toolCallRowIndex.set(event.payload.tool_call_id, rows.length);
        rows.push({
          key: `e${sequence}`,
          kind: "tool_call",
          sequence,
          createdAt,
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
          createdAt,
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
          createdAt,
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
          createdAt,
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
          createdAt,
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
      sequence: feed.latestSequence + 1,
      createdAt: newestCreatedAt(feed)
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
      createdAt: newestCreatedAt(feed),
      text: feed.streamingAgentText
    });
  }

  return rows;
}

function newestCreatedAt(feed: ConversationFeed): number {
  return feed.events[feed.events.length - 1]?.created_at ?? 0;
}

// --- the thread, once the work is put in its place ------------------------------------------

export type ToolCallRow = Extract<TranscriptRow, { kind: "tool_call" }>;

/** What the thread is made of once a turn's work is gathered up.
 *
 * The space between a message and its reply is nearly all tool calls, and showing every
 * one of them in full is how a conversation turns into a log file. So they are not rows
 * here: each turn's tool calls become one thing that knows how to be small.
 */
export type ThreadItem =
  | { kind: "row"; key: string; row: TranscriptRow }
  | {
      kind: "work";
      key: string;
      entries: readonly ToolCallRow[];
      /** The turn these belonged to has finished, so the whole log can fold away. While
       *  it runs the newest entry stays out, because that is what is happening now. */
      settled: boolean;
      /** How long the turn took, in whole seconds, when both ends are known. */
      durationSeconds: number | null;
    };

/** How many of a running turn's tool calls stay visible. The newest one is what is
 *  happening; the ones before it are what happened, and they wait behind a count. */
export const VISIBLE_RUNNING_WORK_ENTRIES = 1;

type OpenTurn = {
  startedAt: number | null;
  workItemIndex: number | null;
};

/** Gather each turn's tool calls into one foldable thing, leaving everything else alone.
 *
 * A turn begins at the prompt that reached the backend and ends at its ending. Its tool
 * calls are collected wherever they fall and anchored at the position of the first one,
 * so the thread still reads top to bottom: you asked, it worked, it answered.
 */
export function threadItems(rows: readonly TranscriptRow[]): ThreadItem[] {
  const items: ThreadItem[] = [];
  let turn: OpenTurn = { startedAt: null, workItemIndex: null };

  function settleTurn(endedAt: number | null): void {
    const at = turn.workItemIndex;
    if (at !== null) {
      const work = items[at];
      if (work?.kind === "work") {
        items[at] = {
          ...work,
          settled: true,
          durationSeconds:
            turn.startedAt === null || endedAt === null
              ? null
              : Math.max(0, endedAt - turn.startedAt)
        };
      }
    }
    turn = { startedAt: null, workItemIndex: null };
  }

  for (const row of rows) {
    if (row.kind === "tool_call") {
      const at = turn.workItemIndex;
      const open = at === null ? null : items[at];
      if (at !== null && open?.kind === "work") {
        items[at] = { ...open, entries: [...open.entries, row] };
        continue;
      }
      turn.workItemIndex = items.length;
      items.push({
        kind: "work",
        key: `work:${row.key}`,
        entries: [row],
        settled: false,
        durationSeconds: null
      });
      continue;
    }

    if (row.kind === "prompt" && turn.startedAt === null) {
      // The prompt that started this turn. A steer's prompt joins one already running,
      // so it is not allowed to reset when the turn began.
      turn.startedAt = row.createdAt;
    }
    items.push({ kind: "row", key: row.key, row });
    if (row.kind === "turn_ended" || row.kind === "turn_stopped") {
      settleTurn(row.createdAt);
    }
  }

  return items;
}

/** How long a turn's work took, in the words a person reads.
 *
 * The record keeps whole seconds, so a turn that took less than one has no duration to
 * report and says so by leaving it out rather than inventing a precision nothing has.
 */
export function workedSentence(durationSeconds: number | null): string {
  if (durationSeconds === null || durationSeconds <= 0) return "worked";
  if (durationSeconds < 60) return `worked for ${durationSeconds}s`;
  const minutes = Math.floor(durationSeconds / 60);
  const seconds = durationSeconds % 60;
  return seconds === 0 ? `worked for ${minutes}m` : `worked for ${minutes}m ${seconds}s`;
}

/** What the affordance over a running turn's older tool calls says. */
export function hiddenWorkSentence(hiddenCount: number): string {
  return `+${hiddenCount} previous tool call${hiddenCount === 1 ? "" : "s"}`;
}

/** The glyph vocabulary a tool row is drawn with — the app's existing step icons. */
export type ToolGlyphKind =
  | "read"
  | "edit"
  | "delete"
  | "move"
  | "search"
  | "execute"
  | "think"
  | "fetch"
  | "switch_mode"
  | "other";

const TOOL_GLYPH_KINDS: readonly ToolGlyphKind[] = [
  "read",
  "edit",
  "delete",
  "move",
  "search",
  "execute",
  "think",
  "fetch",
  "switch_mode",
  "other"
];

// What a tool did, from what it is called. Backends do not agree on this field: one
// sends the protocol's own kinds, another sends the tool's name. Both are read here,
// by what the word means rather than by which backend said it, and a word nobody
// recognises gets the neutral glyph rather than a guess.
const TOOL_NAME_HINTS: readonly (readonly [ToolGlyphKind, readonly string[]])[] = [
  ["execute", ["bash", "shell", "exec", "command", "terminal", "process"]],
  ["search", ["search", "grep", "glob", "find", "list", "ls"]],
  ["fetch", ["fetch", "http", "curl", "download", "web"]],
  ["edit", ["edit", "write", "patch", "apply", "update", "create", "notebook"]],
  ["read", ["read", "view", "open", "cat"]],
  ["delete", ["delete", "remove", "trash"]],
  ["move", ["move", "rename"]],
  ["think", ["think", "plan", "task", "todo", "reason"]]
];

export function toolGlyphKind(toolKind: string): ToolGlyphKind {
  const named = toolKind.trim().toLowerCase();
  const exact = TOOL_GLYPH_KINDS.find((kind) => kind === named);
  if (exact !== undefined) return exact;
  for (const [kind, hints] of TOOL_NAME_HINTS) {
    if (hints.some((hint) => named.includes(hint))) return kind;
  }
  return "other";
}

/** A detail as something a person can read.
 *
 * Backends put structured payloads in here, and a wall of minified JSON is not a
 * sentence — the owner saw one and said so. Structured text is laid out; everything
 * else is already prose and is left exactly as it was written.
 */
export function readableDetail(detail: string | null | undefined): string | null {
  if (detail === null || detail === undefined) return null;
  const trimmed = detail.trim();
  if (trimmed === "") return null;
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return detail;
  try {
    return JSON.stringify(JSON.parse(trimmed), null, 2);
  } catch {
    return detail;
  }
}

/** The label over a prompt bubble, or nothing when it would only say "you".
 *
 * The record keeps every sender exactly as it was written; this is about reading. Your
 * own messages do not need to be labelled as yours — anyone else's do, because who sent
 * a message is real information when it was not you.
 */
export function promptLabelFor(
  senderLabel: string,
  ownSenderLabel: string | null
): string | null {
  return ownSenderLabel !== null && senderLabel === ownSenderLabel ? null : senderLabel;
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
