/** The one line above the composer while the conversation is at rest.
 *
 * At rest the conversation is the composer and this line, and nothing else is on the
 * screen. Nothing here ever opens itself — a permission ask does not push the transcript
 * open when it arrives — so this line is the only place a request that needs a person can
 * appear. An ask that is waiting is on this line or it is nowhere, which is why it
 * outranks everything else the line could say.
 *
 * After that comes the turn that is running, and only when neither is true does the line
 * say what happened last. "Last" means last, whoever produced it: your own message is a
 * thing that happened, and so is a turn that stopped.
 *
 * Nothing here is derived a second time. Whether a turn is running, when it began, the
 * newest tool call is read out of the same thread the transcript draws. The latest plan
 * is read once as conversation status, because it no longer belongs to a turn head.
 */

import {
  explicitReplyMissingSentence,
  liveAskFrom,
  promptLabelFor,
  turnEndingSentence,
  workingSentence,
  PROMPT_DISCARDED_SENTENCE,
  TURN_STOPPED_SENTENCE
} from "./transcript";
import type { ToolCallRow, TranscriptRow } from "./transcript";
import { taskProgressFrom, type ConversationTaskProgress } from "./taskProgress";
import type { ThreadItem } from "./threadLayout";
import {
  conversationThreadItemsForLens,
  type ConversationLens
} from "./lens";
import { presentToolCall } from "./toolCallPresentation";
import { messageContentText } from "./wire";

export type RestLine = {
  /** Who produced it — "you", an agent's label — or null when the line speaks for itself. */
  who: string | null;
  /** The one line. The first line of whatever happened last. */
  text: string;
  /** A quieter fact beside the line, when a caller has one. */
  aside: string | null;
  /** The current plan branch, separate from fallback text so its count stays a control. */
  taskProgress: ConversationTaskProgress | null;
  /** Something is waiting on the person. The bar's most important job. */
  waiting: boolean;
  /** When a turn is running, when it started — so the bar counts as the turn head does. */
  workingSinceUnixMilliseconds: number | null;
  /** What the line shows without words. */
  marks: RestLineMarks;
};

/** The four things the line says without words.
 *
 * They are four independent axes, not one status: a turn can be running while a reply
 * nobody has read is still above it, and a failed turn does not stop something else
 * waiting on an answer. Each one is its own mark or it is nothing.
 */
export type RestLineMarks = {
  /** A turn is running now. */
  running: boolean;
  /** The worker addressed the owner and the owner has not got that far yet. */
  unreadReply: boolean;
  /** A permission or a question is waiting on this person. */
  needsYou: boolean;
  /** The last turn to end, ended failed. */
  failed: boolean;
};

export type RestLinePresentation = {
  /** Rows this lens can put on the line. Complete rows still settle turn structure. */
  visibleRows: readonly TranscriptRow[];
  lens: ConversationLens;
};

/** What this line calls the person reading it.
 *
 * Their own messages are the one thing here that has to say whose they are. The
 * transcript has the opposite problem and the opposite rule — your messages sit on your
 * side of the thread, so labelling them would only be noise — but a line with one thing
 * on it has no sides, and "go and do it" with nothing in front of it reads as the agent
 * saying it.
 */
const YOU = "you";

/** How long the line may be before it is cut.
 *
 * The styling keeps it to one line at whatever width the composer is, so this is not
 * about fitting. It is about what goes into the page at all: a message can be a paragraph
 * its sender never broke, and putting the whole of it in an element that shows the first
 * eighty characters of it is a cost paid on every redraw for something nobody can read.
 */
export const REST_LINE_MAXIMUM_CHARACTERS = 120;

/** The four marks, read from the complete record rather than from what a lens draws.
 *
 * A reply the owner has not read is the record's own idea of one — a message addressed
 * to them, past the position they have read through — and not simply anything with a
 * later sequence than their watermark. Tool calls and turn endings are not replies.
 */
export function restLineMarksFrom(
  rows: readonly TranscriptRow[],
  ownerReadThroughSequence: number,
  turnRunning: boolean,
  needsYou: boolean
): RestLineMarks {
  let unreadReply = false;
  let failed = false;
  // A failure is the last thing that happened to a turn, not the last failure there
  // ever was. The record settles that the same way: look back to whichever comes first,
  // a turn that ended or a message that started one, and only the ending can fail. A
  // message sent after a failure is the person moving on, and the mark goes with them.
  let turnBoundaryFound = false;
  for (let at = rows.length - 1; at >= 0; at -= 1) {
    const row = rows[at];
    if (row === undefined) continue;
    if (!turnBoundaryFound && (row.kind === "turn_ended" || row.kind === "prompt")) {
      failed = row.kind === "turn_ended" && row.ending === "failed";
      turnBoundaryFound = true;
    }
    if (
      !unreadReply
      && row.kind === "agent_message"
      && row.toOwner
      && row.sequence > ownerReadThroughSequence
    ) {
      unreadReply = true;
    }
    if (unreadReply && turnBoundaryFound) break;
  }
  return { running: turnRunning, unreadReply, needsYou, failed };
}

/** What the line says, or nothing at all when nothing has happened yet. */
export function restLineFrom(
  rows: readonly TranscriptRow[],
  ownSenderLabel: string,
  progress: ConversationTaskProgress = taskProgressFrom(rows),
  presentation: RestLinePresentation = { visibleRows: rows, lens: "full" },
  ownerReadThroughSequence = Number.POSITIVE_INFINITY
): RestLine | null {
  const line = restLineBodyFrom(rows, ownSenderLabel, progress, presentation);
  if (line === null) return null;
  return {
    ...line,
    marks: restLineMarksFrom(
      rows,
      ownerReadThroughSequence,
      progress.turnRunning,
      line.waiting
    )
  };
}

/** The line's words and who said them, before the marks are worked out. */
function restLineBodyFrom(
  rows: readonly TranscriptRow[],
  ownSenderLabel: string,
  progress: ConversationTaskProgress,
  presentation: RestLinePresentation
): Omit<RestLine, "marks"> | null {
  const ask = liveAskFrom(presentation.visibleRows);
  if (ask !== null) {
    return {
      who: null,
      // The title is what the ask is; the detail is only read when a backend sent an ask
      // with no title, and then it is the only thing there is to say.
      text: oneLine(ask.title) || oneLine(ask.detail ?? ""),
      aside: null,
      taskProgress: null,
      waiting: true,
      workingSinceUnixMilliseconds: null
    };
  }

  const items = conversationThreadItemsForLens(
    rows,
    presentation.visibleRows,
    presentation.lens
  );
  const turn = newestTurn(items);
  const happened = whateverHappenedLast(presentation.visibleRows, ownSenderLabel);

  if (progress.turnRunning && progress.currentEntry !== null) {
    return {
      who: null,
      text: oneLine(progress.currentEntry.text),
      aside: null,
      taskProgress: progress,
      waiting: false,
      workingSinceUnixMilliseconds: turn?.startedAtUnixMilliseconds ?? null
    };
  }

  if (turn !== null && !turn.settled) {
    const call = newestToolCallOf(items, turn.turnKey);
    const doing = call === null ? null : toolCallSentence(call);
    return {
      // A tool call is the turn's own doing and says so itself. What stands in for it
      // before the turn has called anything belongs to whoever produced it.
      who: doing === null ? happened?.who ?? null : null,
      // What the turn is doing; failing that the last thing anybody can point at, which
      // for a turn that has not called a tool yet is the message that started it.
      text: doing ?? happened?.text ?? workingSentence(null),
      aside: null,
      taskProgress: null,
      waiting: false,
      workingSinceUnixMilliseconds: turn.startedAtUnixMilliseconds
    };
  }

  if (happened === null) return null;
  return {
    who: happened.who,
    text: happened.text,
    aside: null,
    taskProgress: null,
    waiting: false,
    workingSinceUnixMilliseconds: null
  };
}

/** A thing that happened, and who it was that did it. */
type Happened = { who: string | null; text: string };

/** The newest row a person would call a thing that happened.
 *
 * Rows that say nothing in their own right are stepped over rather than drawn as empty:
 * a message that carried only a picture has no first line, and the thing that happened
 * before it is what a person would name if asked.
 */
function whateverHappenedLast(
  rows: readonly TranscriptRow[],
  ownSenderLabel: string
): Happened | null {
  for (let at = rows.length - 1; at >= 0; at -= 1) {
    const row = rows[at];
    if (row === undefined) continue;
    const said = whatThisRowSays(row, ownSenderLabel);
    if (said !== null && said.text !== "") return said;
  }
  return null;
}

/** What one row would put on the line, or nothing when it is not a thing that happened.
 *
 * The kinds that say nothing are the record's bookkeeping — what a turn cost, what it is
 * running on, where the backend cut the thread, and the plan, which is read as progress
 * beside the line rather than as the line. A permission ask says nothing here either: the
 * one that is waiting is the whole of the case above, and an ask that has been answered
 * or has died with its turn is a decision that is over.
 */
function whatThisRowSays(row: TranscriptRow, ownSenderLabel: string): Happened | null {
  switch (row.kind) {
    case "prompt":
      return { who: senderOf(row.senderLabel, ownSenderLabel), text: oneLine(messageContentText(row.content)) };
    case "prompt_refused":
      // Your message did not reach anything. With the conversation closed this line is
      // the only place that could ever say so.
      return {
        who: senderOf(row.senderLabel, ownSenderLabel),
        text: oneLine(`not delivered · ${row.sentence}`)
      };
    case "prompt_uncertain":
      return {
        who: senderOf(row.senderLabel, ownSenderLabel),
        text: "delivery uncertain · do not resend"
      };
    case "prompt_discarded":
      return { who: senderOf(row.senderLabel, ownSenderLabel), text: PROMPT_DISCARDED_SENTENCE };
    case "agent_message":
      return { who: null, text: oneLine(messageContentText(row.content)) };
    case "explicit_reply_missing":
      return { who: null, text: explicitReplyMissingSentence(row.promptSender) };
    case "streaming_agent_message":
      return { who: null, text: oneLine(row.text) };
    case "tool_call":
      return { who: null, text: toolCallSentence(row) };
    case "turn_ended":
      // A turn that simply finished is not news — what it said before finishing is. A
      // turn that failed or was interrupted is the news, and the reason travels with it.
      return row.ending === "completed"
        ? null
        : { who: null, text: oneLine(turnEndingSentence(row.ending, row.errorSummary)) };
    case "turn_stopped":
      return { who: null, text: TURN_STOPPED_SENTENCE };
    case "permission_ask":
    case "user_input":
    case "plan_updated":
    case "model_changed":
    case "token_usage":
    case "context_compacted":
      return null;
  }
}

function senderOf(senderLabel: string, ownSenderLabel: string): string {
  return promptLabelFor(senderLabel, ownSenderLabel) ?? YOU;
}

/** A tool call as the one string this line has room for — the same two halves the row in
 *  the transcript is drawn from, joined the way the pane joins a fact to its reason. */
function toolCallSentence(row: ToolCallRow): string {
  const presentation = presentToolCall(row);
  return oneLine(
    presentation.summary === null
      ? presentation.title
      : `${presentation.title} · ${presentation.summary}`
  );
}

function newestTurn(items: readonly ThreadItem[]): Extract<ThreadItem, { kind: "turn" }> | null {
  for (let at = items.length - 1; at >= 0; at -= 1) {
    const item = items[at];
    if (item?.kind === "turn") return item;
  }
  return null;
}

/** The newest tool call of one turn, and of no other. A call from the turn before this
 *  one, shown under a counter saying this one is working, would be a lie about what is
 *  happening now. */
function newestToolCallOf(items: readonly ThreadItem[], turnKey: string): ToolCallRow | null {
  for (let at = items.length - 1; at >= 0; at -= 1) {
    const item = items[at];
    if (item?.kind !== "work_group" || item.turnKey !== turnKey) continue;
    return item.entries[item.entries.length - 1] ?? null;
  }
  return null;
}

/** Text as one line's worth of it: the first line, and no more of that than fits. */
function oneLine(text: string): string {
  const at = text.indexOf("\n");
  const first = (at === -1 ? text : text.slice(0, at)).trim();
  return first.length <= REST_LINE_MAXIMUM_CHARACTERS
    ? first
    : `${first.slice(0, REST_LINE_MAXIMUM_CHARACTERS - 1).trimEnd()}…`;
}
