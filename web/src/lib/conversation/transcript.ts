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
      /** The instant the person pressed send, in unix milliseconds by the sender's own
       *  clock — or the whole second the row was written in, for the messages whose
       *  senders minted no instant. */
      sentAtUnixMilliseconds: number;
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
      /** The detail the call's start carried, kept when the finish replaces `detail` with
       *  what the tool gave back. A backend that puts the call's own arguments here is
       *  saying what this call was asked to do, which is the thing that says which call it
       *  was — and it must not disappear the moment the call ends. */
      startedDetail: string | null;
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
          mode: event.payload.mode,
          sentAtUnixMilliseconds: sentAtUnixMilliseconds(event.payload, createdAt)
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
          startedDetail: event.payload.detail,
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
          startedDetail: null,
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

/** When a prompt was sent, to the millisecond.
 *
 * A row's `created_at` is a whole second — `storage.py` stamps it with `int(time.time())`,
 * which the wire type's bare `number` does not say and a reader would otherwise have to go
 * and find out. Hence the thousand.
 *
 * A whole second is enough to put a row in its place in a conversation and not enough to
 * count against: a counter anchored to a rounded second is already up to a second wrong
 * before it starts. So the sender puts the instant it measured into the payload, and that
 * is what is counted from where it is there. Where it is not — everything a sender that
 * mints nothing sent, and every row recorded before the field existed — the whole second
 * remains the best there is, which is what has always been counted from.
 *
 * But the minted instant is somebody else's clock, and a clock that is wrong is not a
 * clock that says so. A sender running ahead would freeze the counter on "Working for 0s"
 * for as long as it is ahead; a sender that put seconds where milliseconds belong would
 * open on a number in the tens of thousands of minutes. The row carries a second opinion
 * about when it happened, so the two are held up against each other and an instant that
 * cannot be reconciled with its own row is not believed.
 */
function sentAtUnixMilliseconds(
  payload: { text: string; sent_at_unix_milliseconds?: number },
  createdAt: number
): number {
  const writtenDown = createdAt * 1_000;
  const minted = payload.sent_at_unix_milliseconds;
  if (typeof minted !== "number" || !believable(minted, writtenDown)) return writtenDown;
  return minted;
}

/** How long before its own row a send may claim to have happened and still be believed.
 *
 * A prompt's row is written after the backend has taken the text, so a cold start — a CLI
 * being spawned, a session being loaded — sits inside this gap, and it is exactly the gap
 * the minted instant exists to recover: the person has been waiting since they pressed
 * send, and the row would have the counter open at zero. Generous, therefore. Past this
 * the value is not measuring this send at all.
 */
const SENT_BEFORE_ITS_ROW_LIMIT_MILLISECONDS = 300_000;

/** And how long after its own row, which is a different question with a different answer.
 *
 * A send cannot really happen after the row that records it, so this is not latency, it is
 * the two clocks disagreeing. One second of the allowance is the row's own rounding — the
 * stamp is a whole second, so it can sit up to a second before the moment it was written —
 * and the other is ordinary skew. Every millisecond past it is a millisecond the counter
 * would sit frozen on zero.
 */
const SENT_AFTER_ITS_ROW_LIMIT_MILLISECONDS = 2_000;

function believable(minted: number, writtenDown: number): boolean {
  const apart = minted - writtenDown;
  return apart <= SENT_AFTER_ITS_ROW_LIMIT_MILLISECONDS
    && apart >= -SENT_BEFORE_ITS_ROW_LIMIT_MILLISECONDS;
}

// --- the thread, once the work is put in its place ------------------------------------------

export type ToolCallRow = Extract<TranscriptRow, { kind: "tool_call" }>;

/** What the thread is made of once a turn's work is gathered up.
 *
 * The space between a message and its reply is nearly all tool calls, and showing every
 * one of them in full is how a conversation turns into a log file. So they are not rows
 * here: each turn's tool calls become one thing that knows how to be small.
 *
 * The same is true of what the turn said while it worked. A turn that thought out loud
 * for five paragraphs before answering is exactly as long to scroll past as one that made
 * five tool calls, so once the turn settles its commentary goes behind the same fold, and
 * what stays is the last thing it said — the answer somebody came back for.
 */
export type ThreadItem =
  | {
      kind: "row";
      key: string;
      row: TranscriptRow;
      /** The turn whose fold this row goes behind, or nothing when it never folds.
       *
       * What the agent said along the way is part of the work rather than part of the
       * answer, so a settled turn keeps only the last thing it said and the rest go behind
       * the same fold its tool calls go behind. What the person did never folds: their
       * message started the turn, and the permission they were asked for is a decision they
       * made rather than something the turn produced. */
      behindTheFoldOf: string | null;
    }
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
      /** When the turn began, in milliseconds, so a live counter can be honest after a
       *  reload. This is the sender's browser's clock, and `durationSeconds` below is the
       *  record's own, so the two are never subtracted from each other: a settled turn's
       *  length is two rows on one clock, and this is what a counter counts from. */
      startedAtUnixMilliseconds: number | null;
      /** How the turn ended, for the turns that ended. */
      ending: ConversationTurnEnding | null;
      /** The newest turn in the conversation. Only it takes the stopped wording. */
      isLatest: boolean;
      durationSeconds: number | null;
      toolCallCount: number;
      /** How many of the turn's own messages went behind the fold, so the label can say
       *  what is behind it rather than counting only the tool calls. */
      foldedMessageCount: number;
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
  startedAtUnixMilliseconds: number | null;
  anchorIndex: number | null;
  groupIndexes: number[];
  openGroupIndex: number | null;
  /** Where this turn's own messages landed, in the order it said them. The last is the
   *  answer and stays; the ones before it are commentary and fold. */
  messageIndexes: number[];
};

const NO_TURN: OpenTurn = {
  turnKey: "turn:none",
  startedAt: null,
  startedAtUnixMilliseconds: null,
  anchorIndex: null,
  groupIndexes: [],
  openGroupIndex: null,
  messageIndexes: []
};

/** Lay the thread out: a head for every turn, and its work in the places it happened.
 *
 * Two things are being balanced. A turn needs one place that does not move, so a person
 * has something to hold from the moment they send to the moment it is done. And the work
 * needs to stay where it fell, so the tool calls between two pieces of commentary read as
 * having happened between them. So the head is emitted once, at the turn's start, and
 * everything the turn did is emitted in place — and when the turn ends, the head becomes
 * the fold and the work goes behind it.
 *
 * Nothing is ever moved to make that happen. A row that goes behind the fold is marked
 * where it already sits, so opening the fold puts every piece of commentary back between
 * the runs of tool calls it sat between rather than gathered up at the end.
 */
export function threadItems(rows: readonly TranscriptRow[]): ThreadItem[] {
  const items: ThreadItem[] = [];
  let turn: OpenTurn = { ...NO_TURN };

  function settleTurn(
    endedAt: number | null,
    stopped: boolean,
    ending: ConversationTurnEnding | null
  ): void {
    // Everything the turn said except the last of it. A turn that said nothing folds
    // nothing, which is the whole rule an interrupted turn and a tools-only turn need:
    // there is no answer to keep, so nothing is kept, and the fold holds the lot.
    const folded = turn.messageIndexes.slice(0, -1);
    for (const messageAt of folded) {
      const said = items[messageAt];
      if (said?.kind === "row") items[messageAt] = { ...said, behindTheFoldOf: turn.turnKey };
    }
    const at = turn.anchorIndex;
    if (at !== null) {
      const anchor = items[at];
      if (anchor?.kind === "turn") {
        items[at] = {
          ...anchor,
          settled: true,
          stopped,
          ending,
          foldedMessageCount: folded.length,
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
        groupIndexes: [],
        messageIndexes: []
      };
      items.push({
        kind: "turn",
        key: `turn:${row.key}`,
        turnKey: `turn:${row.key}`,
        settled: false,
        stopped: false,
        plan: row.entries,
        startedAtUnixMilliseconds: null,
        ending: null,
        isLatest: false,
        durationSeconds: null,
        toolCallCount: 0,
        foldedMessageCount: 0
      });
      continue;
    }

    // Nothing folds as it arrives: a running turn shows everything it has done, and the
    // collapse happens once, when the turn settles.
    items.push({ kind: "row", key: row.key, row, behindTheFoldOf: null });

    if (row.kind === "agent_message" && turn.anchorIndex !== null) {
      turn.messageIndexes = [...turn.messageIndexes, items.length - 1];
    }

    if (row.kind === "prompt" && turn.startedAt === null) {
      // The prompt that started this turn. A steer's prompt joins one already running,
      // so it is not allowed to reset when the turn began, nor to open a second head.
      turn = {
        turnKey: `turn:${row.key}`,
        startedAt: row.createdAt,
        startedAtUnixMilliseconds: row.sentAtUnixMilliseconds,
        anchorIndex: items.length,
        groupIndexes: [],
        openGroupIndex: null,
        messageIndexes: []
      };
      items.push({
        kind: "turn",
        key: `turn:${row.key}`,
        turnKey: `turn:${row.key}`,
        settled: false,
        stopped: false,
        plan: null,
        startedAtUnixMilliseconds: row.sentAtUnixMilliseconds,
        ending: null,
        isLatest: false,
        durationSeconds: null,
        toolCallCount: 0,
        foldedMessageCount: 0
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

/** How far past the second the counter's tick is aimed.
 *
 * A timer asked for exactly the boundary can fire a hair before it, read the second it
 * has not quite reached, and show the same number twice — then skip one catching up.
 */
export const LIVE_COUNTER_TICK_MARGIN_MILLISECONDS = 20;

/** How long a turn has been running, in the whole seconds the counter says.
 *
 * One subtraction and one floor. Flooring the two instants separately — the clock to its
 * own second, the start to its own — is what made the number repeat and skip: the second
 * it crossed had nothing to do with the moment the turn began.
 */
export function elapsedSecondsSince(startedAtUnixMilliseconds: number, now: number): number {
  return Math.max(0, Math.floor((now - startedAtUnixMilliseconds) / 1_000));
}

/** How long until the counter's next tick, measured from the start rather than from now.
 *
 * Every wait is worked out from the instant the turn began, so the ticks land on that
 * instant's own seconds instead of the wall clock's. It also means nothing accumulates: a
 * tab whose timers were throttled while it was in the background comes back to the right
 * number rather than to the number of ticks it managed to fire.
 */
export function millisecondsUntilNextSecond(
  startedAtUnixMilliseconds: number,
  now: number
): number {
  const since = now - startedAtUnixMilliseconds;
  const pastTheSecond = ((since % 1_000) + 1_000) % 1_000;
  return 1_000 - pastTheSecond + LIVE_COUNTER_TICK_MARGIN_MILLISECONDS;
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

/** What is behind an opened turn's fold, counted by what it is.
 *
 * The fold used to hold tool calls and say so. It now also holds everything the turn said
 * on the way to its answer, and a label that still counted only the calls would be telling
 * a smaller truth than the fold holds. Each kind is named and counted; a kind there is
 * none of is not mentioned at all.
 */
export function foldedWorkSentence(toolCallCount: number, messageCount: number): string {
  const counted: string[] = [];
  if (toolCallCount > 0) {
    counted.push(`${toolCallCount} tool call${toolCallCount === 1 ? "" : "s"}`);
  }
  if (messageCount > 0) {
    counted.push(`${messageCount} message${messageCount === 1 ? "" : "s"}`);
  }
  return counted.join(" · ");
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

// --- what a tool call's line says -------------------------------------------------------------

/** A tool call as one line: what happened, and which call it was.
 *
 * A row that reads only "Bash" says nothing, because every Bash call reads that. The line
 * a person needs is a phrase for the kind of thing that happened and, beside it, the one
 * value that says which call this was — the command that ran, the path that was read, the
 * pattern that was searched for.
 */
export type ToolCallLine = {
  /** What happened, in the fewest words that say it. */
  title: string;
  /** The one value that says which call this was, or nothing when the title already
   *  contains it. */
  summary: string | null;
};

/** How long a summary may be, so a row is always one line. */
export const TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS = 80;

/** What each kind of call did, for the rows whose backend titled them with the tool's name.
 *
 * The same classification decides the glyph, so a row's picture and its words cannot
 * disagree. Two kinds get no phrase on purpose: "think" and "other" hold calls too
 * unalike for one verb to be true of all of them, and the tool's own name beats a wrong
 * verb.
 */
/** What happened, said the way a person would say it.
 *
 * Each is a phrase rather than a verb, because "Ran" and "Read" are the halves of
 * sentences and a header is not half of anything. This is the brighter part of the line;
 * the value beside it says which call it was. */
const TOOL_CALL_PHRASES: Partial<Record<ToolGlyphKind, string>> = {
  read: "Read file",
  edit: "Edited file",
  delete: "Deleted file",
  move: "Moved file",
  search: "Searched files",
  execute: "Ran command",
  fetch: "Fetched page",
  switch_mode: "Switched mode"
};

/** The argument names that name a call's subject, best first, and the tool behind each.
 *
 * Only claude sends a call's arguments at all, so this is its tool set. Four of these are
 * in conversations already recorded here — `command` and `description` on Bash, `file_path`
 * on Read and Write, `query` on ToolSearch, `subject` on TaskCreate. The other three are
 * named by the tools they belong to: `pattern` is what Grep and Glob are asked to find,
 * `url` is what WebFetch is pointed at, and `notebook_path` is NotebookEdit's own spelling
 * of the file it edits.
 *
 * Nothing outside this list is read, and there is no fallback to whatever text an
 * unrecognised call happens to carry: a value nobody here has named as a subject may be
 * anything at all — including the payload the tool was handed — and a line in a
 * conversation is not the place to find that out.
 */
const CALL_SUBJECT_ARGUMENT_NAMES: readonly string[] = [
  "command",
  "file_path",
  "notebook_path",
  "pattern",
  "url",
  "query",
  "subject",
  "description"
];

/** The shells a command arrives wrapped in, each up to the flag that carries it. */
const SHELL_INVOCATIONS: readonly RegExp[] = [
  /^(?:\S*\/)?(?:bash|sh|zsh)\s+-[a-z]*c\s+([\s\S]+)$/,
  /^(?:\S*[/\\])?cmd(?:\.exe)?\s+\/c\s+([\s\S]+)$/i,
  /^(?:\S*[/\\])?pwsh(?:\.exe)?\s+-(?:command|c)\s+([\s\S]+)$/i
];

/** The command a shell invocation carried, or the text itself when it is not one.
 *
 * Codex runs everything through a login shell, so without this every one of its rows
 * opens with the same `/bin/zsh -lc` before saying anything about the call. The shell is
 * how the command was carried; the command is what was done.
 */
export function commandWithoutShellInvocation(text: string): string {
  const trimmed = text.trim();
  for (const invocation of SHELL_INVOCATIONS) {
    const wrapped = invocation.exec(trimmed);
    if (wrapped) return unquoted(wrapped[1].trim());
  }
  return text;
}

function unquoted(text: string): string {
  const quote = text[0];
  if ((quote !== '"' && quote !== "'") || text.length < 2 || !text.endsWith(quote)) return text;
  const inside = text.slice(1, -1);
  // A double-quoted argument escapes its own quotes and backslashes, and those escapes
  // belong to the quoting rather than to the command.
  return quote === '"' ? inside.replace(/\\(["\\])/g, "$1") : inside;
}

/** The one value that says which call this was, read out of what the call was asked to do.
 *
 * A backend that sends the call's arguments sends them as a JSON object, and the subject
 * is one named value inside it — which is why a claude row could say nothing before: the
 * whole object was offered as the line, and an object is never one short line.
 *
 * The start is read before the finish, because the finish carries what the tool gave back
 * and that answers a different question. So a row says the same thing while the call runs
 * as it does once the call is over.
 */
function identifyingFact(startedDetail: string | null, detail: string | null): string | null {
  for (const written of [startedDetail, detail]) {
    if (written === null) continue;
    const trimmed = written.trim();
    if (trimmed === "") continue;
    const given = callArguments(trimmed);
    if (given === null) continue;
    const subject = subjectOf(given);
    if (subject !== null) return subject;
  }
  return null;
}

/** A backend saying in one line what the call is, where it sends no arguments at all.
 *
 * One line is a description. Several lines is output, and output belongs behind the row
 * rather than on it. Arguments are skipped here because they are read properly above; a
 * JSON object that named no subject is not a description of anything.
 */
function spokenDetail(startedDetail: string | null, detail: string | null): string | null {
  for (const written of [startedDetail, detail]) {
    if (written === null) continue;
    const trimmed = written.trim();
    if (trimmed === "" || trimmed.includes("\n") || callArguments(trimmed) !== null) continue;
    return trimmed;
  }
  return null;
}

/** A backend's own title with the kind it already stated taken off the front.
 *
 * Hermes writes both halves of this line itself — "read: /path/to/file", "search: wire.ts"
 * — which is the same shape the pane draws, in its words instead of ours. Where the header
 * now says the kind, the backend's copy of it in front of the value is the same word
 * twice, so it comes off and the value is what remains.
 */
function withoutTheKindItStated(text: string, toolKind: string): string {
  const stated = /^([\p{L}_]+)\s*:\s*(\S[\s\S]*)$/u.exec(text);
  if (!stated) return text;
  const front = stated[1].toLowerCase();
  const kindsItCouldBe = new Set<string>([toolKind.trim().toLowerCase(), toolGlyphKind(toolKind)]);
  return kindsItCouldBe.has(front) || TOOL_KIND_WORDS.has(front) ? stated[2].trim() : text;
}

/** The words a backend uses in front of a value to say what kind of call it was. Each is a
 *  kind this pane already draws a header for, so seeing one means the header has it. */
const TOOL_KIND_WORDS: ReadonlySet<string> = new Set([
  "read",
  "write",
  "edit",
  "delete",
  "move",
  "search",
  "grep",
  "glob",
  "execute",
  "terminal",
  "command",
  "shell",
  "bash",
  "fetch"
]);

function callArguments(trimmed: string): Record<string, unknown> | null {
  if (!trimmed.startsWith("{")) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function subjectOf(given: Record<string, unknown>): string | null {
  for (const name of CALL_SUBJECT_ARGUMENT_NAMES) {
    const value = given[name];
    if (typeof value !== "string") continue;
    const said = firstLine(value);
    if (said !== "") return said;
  }
  return null;
}

function firstLine(text: string): string {
  const at = text.indexOf("\n");
  return (at === -1 ? text : text.slice(0, at)).trim();
}

function shortened(text: string): string {
  return text.length <= TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS
    ? text
    : `${text.slice(0, TOOL_CALL_SUMMARY_MAXIMUM_CHARACTERS - 1).trimEnd()}…`;
}

/** Text as its words: runs of letters and digits, lowercased, punctuation gone. A colon, a
 *  shell prompt or a pair of quotes must not make a repeat look like news. */
function words(text: string): string[] {
  return text.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [];
}

/** Whether `said` adds nothing to `within`, because its words are already in there — in
 *  order and together, as a run rather than scattered.
 *
 * Comparing the two as one long string of letters would be simpler and wrong: it makes
 * "Searched" swallow a pattern of "arch", and "Read" swallow a file called "edit.ts",
 * which leaves a row reading as a bare verb with nothing beside it — the exact thing the
 * summary exists to prevent. A word is the smallest unit that repeats meaningfully.
 */
function addsNothingTo(said: string, within: string): boolean {
  const sought = words(said);
  const already = words(within);
  if (sought.length === 0) return true;
  if (sought.length > already.length) return false;
  for (let from = 0; from <= already.length - sought.length; from += 1) {
    if (sought.every((word, at) => already[from + at] === word)) return true;
  }
  return false;
}

/** The line this call is drawn as: what happened, then which call it was.
 *
 * Both halves, always. The header says the kind of thing that happened in words a person
 * would use — "Ran command", "Read file" — and the value beside it says which one: the
 * command that ran, the path that was read. A row showing only the raw thing makes the
 * reader work out what kind of call it was from its argument; a row showing only the
 * phrase does not say which call it was. Neither half is worth having alone.
 *
 * Where a backend has no phrase this pane knows — an unrecognised tool — its own title
 * stands as the header, because the agent that made the call still says it better than
 * nothing. And where the identifying value cannot be read out of the arguments, the
 * backend's title takes that place instead, since hermes writes "read: /path/to/file" and
 * codex writes the command, and either is exactly the value the second half is for.
 */
export function toolCallLine(row: {
  title: string;
  toolKind: string;
  startedDetail?: string | null;
  detail: string | null;
}): ToolCallLine {
  const written = withoutTheKindItStated(
    commandWithoutShellInvocation(row.title.trim()),
    row.toolKind
  );
  const titleNamesTheTool = row.title.trim().toLowerCase() === row.toolKind.trim().toLowerCase();
  const phrase = TOOL_CALL_PHRASES[toolGlyphKind(row.toolKind)];
  // The arguments name the call best, because they are what it was asked to do. Failing
  // those, what the backend titled it is the value — codex titles a call with the command
  // it ran, hermes with the path it read. A title that is only the tool's name says
  // nothing, and then a one-line description is the last thing left to try.
  const spoken =
    identifyingFact(row.startedDetail ?? null, row.detail) ??
    (titleNamesTheTool ? spokenDetail(row.startedDetail ?? null, row.detail) : written);
  const summary = spoken === null ? null : shortened(commandWithoutShellInvocation(spoken));
  // The phrase is drawn from the icon's kind, which is a loose reading of a tool's name —
  // near enough to pick a glyph, not always near enough to put in words. With a value
  // beside it the pair is self-correcting: "Ran command · git status" is right even if the
  // kind were wrong. Alone it is an unchecked claim, so a call whose value cannot be read
  // keeps the name the backend gave it, which at least is a fact.
  const title = phrase !== undefined && summary !== null ? phrase : written;
  return {
    title,
    summary: summary !== null && addsNothingTo(summary, title) ? null : summary
  };
}

/** Whether a line already shows the whole of a detail, so opening the row would only
 *  repeat it. The detail a backend writes in one short line is usually the same thing the
 *  line is drawn from, and an expander that opens onto what is already on the screen is
 *  an affordance that does nothing. */
export function lineShowsWholeDetail(line: ToolCallLine, detail: string): boolean {
  return addsNothingTo(detail, `${line.title} ${line.summary ?? ""}`);
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
