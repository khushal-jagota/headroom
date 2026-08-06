/** Lay transcript rows out as stable turn heads, chronological work runs, and rows.
 *
 * A turn's work stays where it happened while one stable head owns whether that work
 * is folded. This module contains that layout policy and nothing about how the resulting
 * items are worded or rendered.
 */

import type { TranscriptRow, ToolCallRow } from "../transcript";
import type { ConversationTurnEnding } from "../wire";

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
   *  the first visible transcript row, so the work between two pieces of the agent's
   *  own commentary stays between them rather than being gathered elsewhere. */
  | {
      kind: "work_group";
      key: string;
      turnKey: string;
      entries: readonly ToolCallRow[];
      /** Its turn is over, so it belongs behind that turn's fold. */
      settled: boolean;
    };

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
      if (said?.kind === "row") {
        items[messageAt] = { ...said, behindTheFoldOf: turn.turnKey };
      }
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
      if (group?.kind === "work_group") {
        items[groupAt] = { ...group, settled: true };
      }
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

    // Rows that never appear in the transcript do not interrupt the visible work run.
    if (row.kind !== "plan_updated" && row.kind !== "token_usage") {
      turn.openGroupIndex = null;
    }

    if (row.kind === "plan_updated") {
      // The latest plan belongs to conversation status. It is not thread history and it
      // does not interrupt a visible run of tool calls.
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

  return items;
}
