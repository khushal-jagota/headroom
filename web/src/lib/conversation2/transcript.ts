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
  PlanEntry,
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
  /** The agent's plan as of this row. It is never drawn as a line of its own — the plan
   *  strip is its rendering — so it exists here only to reach the turn it belongs to. */
  | { key: string; kind: "plan_updated"; sequence: number; createdAt: number; entries: readonly PlanEntry[] }
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
      case "plan_updated":
        rows.push({
          key: `e${sequence}`,
          kind: "plan_updated",
          sequence,
          createdAt,
          entries: event.payload.entries
        });
        break;
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
  /** A turn's stable head. It appears the moment the turn starts, before there is
   *  anything to put under it, and it is still there — as the fold — when the turn is
   *  over. Nothing about it moves while the turn runs. */
  | {
      kind: "turn";
      key: string;
      turnKey: string;
      settled: boolean;
      /** The turn stopped without an ending, so there is no length anybody can claim. */
      stopped: boolean;
      /** The plan as this turn last stated it, when this is the anchor holding the
       *  newest one. A conversation has one plan, so only one anchor ever shows it. */
      plan: readonly PlanEntry[] | null;
      /** When the turn began, so a live counter can be honest after a reload. */
      startedAt: number | null;
      /** How the turn ended, for the turns that ended. */
      ending: ConversationTurnEnding | null;
      /** The newest turn in the conversation. Only it takes the stopped wording. */
      isLatest: boolean;
      durationSeconds: number | null;
      toolCallCount: number;
    }
  /** One unbroken run of tool calls, sitting exactly where it happened. A run ends at
   *  the first thing that is not a tool call, so the work between two pieces of the
   *  agent's own commentary stays between them rather than being gathered elsewhere. */
  | {
      kind: "work_group";
      key: string;
      turnKey: string;
      entries: readonly ToolCallRow[];
      /** Its turn is over, so it belongs behind that turn's fold. */
      settled: boolean;
    };

/** How many of a running turn's tool calls stay visible in each run. The newest one is
 *  what is happening; the ones before it are what happened, and they wait behind a count. */
export const VISIBLE_RUNNING_WORK_ENTRIES = 1;

type OpenTurn = {
  turnKey: string;
  startedAt: number | null;
  anchorIndex: number | null;
  groupIndexes: number[];
  openGroupIndex: number | null;
};

const NO_TURN: OpenTurn = {
  turnKey: "turn:none",
  startedAt: null,
  anchorIndex: null,
  groupIndexes: [],
  openGroupIndex: null
};

/** Lay the thread out: a head for every turn, and its work in the places it happened.
 *
 * Two things are being balanced. A turn needs one place that does not move, so a person
 * has something to hold from the moment they send to the moment it is done. And the work
 * needs to stay where it fell, so the tool calls between two pieces of commentary read as
 * having happened between them. So the head is emitted once, at the turn's start, and the
 * runs of tool calls are emitted in place — and when the turn ends, the head becomes the
 * fold and the runs go behind it.
 */
export function threadItems(rows: readonly TranscriptRow[]): ThreadItem[] {
  const items: ThreadItem[] = [];
  let turn: OpenTurn = { ...NO_TURN };

  function settleTurn(
    endedAt: number | null,
    stopped: boolean,
    ending: ConversationTurnEnding | null
  ): void {
    const at = turn.anchorIndex;
    if (at !== null) {
      const anchor = items[at];
      if (anchor?.kind === "turn") {
        items[at] = {
          ...anchor,
          settled: true,
          stopped,
          ending,
          // A turn nobody saw the end of has no length anybody can claim.
          durationSeconds:
            stopped || turn.startedAt === null || endedAt === null
              ? null
              : Math.max(0, endedAt - turn.startedAt)
        };
      }
    }
    for (const groupAt of turn.groupIndexes) {
      const group = items[groupAt];
      if (group?.kind === "work_group") items[groupAt] = { ...group, settled: true };
    }
    turn = { ...NO_TURN };
  }

  function countToolCalls(): void {
    const at = turn.anchorIndex;
    if (at === null) return;
    const anchor = items[at];
    if (anchor?.kind !== "turn") return;
    let total = 0;
    for (const groupAt of turn.groupIndexes) {
      const group = items[groupAt];
      if (group?.kind === "work_group") total += group.entries.length;
    }
    items[at] = { ...anchor, toolCallCount: total };
  }

  for (const row of rows) {
    if (row.kind === "tool_call") {
      const openAt = turn.openGroupIndex;
      const open = openAt === null ? null : items[openAt];
      if (openAt !== null && open?.kind === "work_group") {
        items[openAt] = { ...open, entries: [...open.entries, row] };
      } else {
        turn.openGroupIndex = items.length;
        turn.groupIndexes = [...turn.groupIndexes, items.length];
        items.push({
          kind: "work_group",
          key: `work:${row.key}`,
          turnKey: turn.turnKey,
          entries: [row],
          settled: false
        });
      }
      countToolCalls();
      continue;
    }

    // Anything that is not a tool call breaks the run it interrupted.
    turn.openGroupIndex = null;

    if (row.kind === "plan_updated") {
      // A plan replaces the plan; it is never merged into the one before it. It is also
      // never a line of its own — the strip is how a plan is read.
      const at = turn.anchorIndex;
      const anchor = at === null ? null : items[at];
      if (at !== null && anchor?.kind === "turn") {
        items[at] = { ...anchor, plan: row.entries };
        continue;
      }
      turn = {
        ...turn,
        turnKey: `turn:${row.key}`,
        anchorIndex: items.length,
        groupIndexes: []
      };
      items.push({
        kind: "turn",
        key: `turn:${row.key}`,
        turnKey: `turn:${row.key}`,
        settled: false,
        stopped: false,
        plan: row.entries,
        startedAt: null,
        ending: null,
        isLatest: false,
        durationSeconds: null,
        toolCallCount: 0
      });
      continue;
    }

    items.push({ kind: "row", key: row.key, row });

    if (row.kind === "prompt" && turn.startedAt === null) {
      // The prompt that started this turn. A steer's prompt joins one already running,
      // so it is not allowed to reset when the turn began, nor to open a second head.
      turn = {
        turnKey: `turn:${row.key}`,
        startedAt: row.createdAt,
        anchorIndex: items.length,
        groupIndexes: [],
        openGroupIndex: null
      };
      items.push({
        kind: "turn",
        key: `turn:${row.key}`,
        turnKey: `turn:${row.key}`,
        settled: false,
        stopped: false,
        plan: null,
        startedAt: row.createdAt,
        ending: null,
        isLatest: false,
        durationSeconds: null,
        toolCallCount: 0
      });
      continue;
    }

    if (row.kind === "turn_ended" || row.kind === "turn_stopped") {
      settleTurn(
        row.createdAt,
        row.kind === "turn_stopped",
        row.kind === "turn_ended" ? row.ending : null
      );
    }
  }

  // The newest turn is the only one that takes the stopped wording.
  for (let at = items.length - 1; at >= 0; at -= 1) {
    const item = items[at];
    if (item?.kind !== "turn") continue;
    items[at] = { ...item, isLatest: true };
    break;
  }

  // One conversation, one plan: the newest plan row is the plan, and the heads that
  // stated earlier ones are history rather than a second strip.
  let seenNewestPlan = false;
  for (let at = items.length - 1; at >= 0; at -= 1) {
    const item = items[at];
    if (item?.kind !== "turn" || item.plan === null) continue;
    if (seenNewestPlan) items[at] = { ...item, plan: null };
    seenNewestPlan = true;
  }

  return items;
}

/** A length of time, in the one format this pane says them in.
 *
 * Whole seconds throughout: the record keeps integer unix seconds, so a tenth of a second
 * is not a thing anybody here can know. Under a minute is just seconds; past that it is
 * minutes and seconds, with the seconds left off when there are none.
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder === 0 ? `${minutes}m` : `${minutes}m ${remainder}s`;
}

/** How long a turn's work took. A turn too short to measure claims no length at all. */
export function workedSentence(durationSeconds: number | null): string {
  if (durationSeconds === null || durationSeconds <= 0) return "Worked";
  return `Worked for ${formatDuration(durationSeconds)}`;
}

/** The same, for the turn a person stopped themselves. It says who did it. */
export function stoppedSentence(durationSeconds: number | null): string {
  if (durationSeconds === null || durationSeconds <= 0) return "You stopped this response";
  return `You stopped after ${formatDuration(durationSeconds)}`;
}

/** The live counter on a turn that is still going. */
export function workingSentence(elapsedSeconds: number | null): string {
  if (elapsedSeconds === null || elapsedSeconds < 0) return "Working";
  return `Working for ${formatDuration(elapsedSeconds)}`;
}

/** The label over a settled turn's fold — which turn it is decides how it reads.
 *
 * Only the latest turn takes the stopped wording. Further back in a conversation "you
 * stopped this" is a strange thing to read next to five other turns; the interrupted
 * line on the turn itself already said it, and there it stays.
 */
export function turnFoldLabel(item: {
  durationSeconds: number | null;
  ending: ConversationTurnEnding | null;
  isLatest: boolean;
}): string {
  return item.isLatest && item.ending === "interrupted"
    ? stoppedSentence(item.durationSeconds)
    : workedSentence(item.durationSeconds);
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
