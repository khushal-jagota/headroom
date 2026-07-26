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
 * plan it is working through and the newest tool call it made are all read out of the
 * same thread the transcript is drawn from, so the bar and the turn head cannot disagree.
 */

import {
  liveAskFrom,
  promptLabelFor,
  threadItems,
  toolCallLine,
  turnEndingSentence,
  workingSentence,
  TURN_STOPPED_SENTENCE
} from "./transcript";
import type { ThreadItem, ToolCallRow, TranscriptRow } from "./transcript";
import { messageContentText } from "./wire";

export type RestLine = {
  /** Who produced it — "you", an agent's label — or null when the line speaks for itself. */
  who: string | null;
  /** The one line. The first line of whatever happened last. */
  text: string;
  /** A quieter fact beside it: the plan's progress while a turn runs. */
  aside: string | null;
  /** Something is waiting on the person. The bar's most important job. */
  waiting: boolean;
  /** When a turn is running, when it started — so the bar counts as the turn head does. */
  workingSinceUnixMilliseconds: number | null;
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

/** The words for a message that was taken back before anything received it, which is what
 *  the transcript says about the same row. */
const DISCARDED_SENTENCE = "discarded without being delivered";

/** How long the line may be before it is cut.
 *
 * The styling keeps it to one line at whatever width the composer is, so this is not
 * about fitting. It is about what goes into the page at all: a message can be a paragraph
 * its sender never broke, and putting the whole of it in an element that shows the first
 * eighty characters of it is a cost paid on every redraw for something nobody can read.
 */
export const REST_LINE_MAXIMUM_CHARACTERS = 120;

/** What the line says, or nothing at all when nothing has happened yet. */
export function restLineFrom(
  rows: readonly TranscriptRow[],
  ownSenderLabel: string
): RestLine | null {
  const ask = liveAskFrom(rows);
  if (ask !== null) {
    return {
      who: null,
      // The title is what the ask is; the detail is only read when a backend sent an ask
      // with no title, and then it is the only thing there is to say.
      text: oneLine(ask.title) || oneLine(ask.detail ?? ""),
      aside: null,
      waiting: true,
      workingSinceUnixMilliseconds: null
    };
  }

  const items = threadItems(rows);
  const turn = newestTurn(items);
  const happened = whateverHappenedLast(rows, ownSenderLabel);

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
      aside: planProgress(items),
      waiting: false,
      workingSinceUnixMilliseconds: turn.startedAtUnixMilliseconds
    };
  }

  if (happened === null) return null;
  return {
    who: happened.who,
    text: happened.text,
    aside: null,
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
    case "prompt_discarded":
      return { who: senderOf(row.senderLabel, ownSenderLabel), text: DISCARDED_SENTENCE };
    case "agent_message":
      return { who: null, text: oneLine(messageContentText(row.content)) };
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
  const line = toolCallLine(row);
  return oneLine(line.summary === null ? line.title : `${line.title} · ${line.summary}`);
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

/** How far through its plan the conversation is, in the words the plan strip says it in.
 *
 * A conversation has one plan, so there is one head holding it however many turns ago it
 * was stated, and that is the one this reads. */
function planProgress(items: readonly ThreadItem[]): string | null {
  for (const item of items) {
    if (item.kind !== "turn" || item.plan === null || item.plan.length === 0) continue;
    const done = item.plan.filter((entry) => entry.status === "completed").length;
    return `${done} / ${item.plan.length} tasks`;
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
