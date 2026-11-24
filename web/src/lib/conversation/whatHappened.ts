/** What a transcript row says in one line: the words the conversation status, the dock and
 *  the opened pane use when they name the newest thing that happened, and a tool call's one
 *  sentence. */

import {
  messageNotDelivered,
  promptLabelFor,
  refusalSentence,
  turnFailureSentence,
  PROMPT_DISCARDED_SENTENCE,
  TURN_STOPPED_SENTENCE
} from "./transcript";
import type { ToolCallRow, TranscriptRow } from "./transcript";
import { presentToolCall, presentTense } from "./toolCallPresentation";
import { messageContentText } from "./wire";

export { presentTense };

/** What a line calls the person reading it. A line with one thing on it has no sides, so the
 *  person's own message says whose it is. */
const YOU = "you";

/** How long a line may be before it is cut. A message can be a paragraph its sender never
 *  broke; the line keeps its first line and no more than this. */
const LINE_MAXIMUM_CHARACTERS = 120;

/** A thing that happened, and who did it. */
type Happened = { who: string | null; text: string };

/** Whether the last turn to end ended failed, read from the complete record rather than
 *  from what a lens draws. A failure is the last thing that happened to a turn: look back to
 *  whichever comes first, a turn that ended or a message that started one. A message sent
 *  after a failure is the person moving on. */
export function lastTurnFailedFrom(rows: readonly TranscriptRow[]): boolean {
  for (let at = rows.length - 1; at >= 0; at -= 1) {
    const row = rows[at];
    if (row?.kind === "turn_ended") return row.ending === "failed";
    if (row?.kind === "prompt" || row?.kind === "agent_turn_started") return false;
  }
  return false;
}

/** What one row would put on the line, or nothing when it is not a thing that happened.
 *
 * The kinds that say nothing are the record's bookkeeping — what a turn cost, what it is
 * running on, where the backend cut the thread, and the plan, which is read as progress
 * beside the line rather than as the line. A permission ask or a question says nothing here
 * either: one that waits is drawn as the ask itself, and one that was answered or died with
 * its turn is a decision that is over.
 */
export function whatThisRowSays(row: TranscriptRow, ownSenderLabel: string): Happened | null {
  switch (row.kind) {
    case 'credential_request':
      return { who: null, text: row.request.state === 'pending' ? `${row.request.provider} API key requested` : `${row.request.name} · ${row.request.state === 'reused' ? 'saved' : row.request.state}` };
    case "agent_turn_started":
      return { who: null, text: oneLine(row.reason) };
    case "background_task_started":
    case "background_task_ended":
      return null;
    case "prompt":
      return { who: senderOf(row.senderLabel, ownSenderLabel), text: oneLine(messageContentText(row.content)) };
    case "prompt_refused":
      // Your message did not reach anything. With the conversation closed this line is
      // the only place that could ever say so.
      return {
        who: senderOf(row.senderLabel, ownSenderLabel),
        text: oneLine(messageNotDelivered(refusalSentence(row.reason)))
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
    case "streaming_agent_message":
      return { who: null, text: oneLine(row.text) };
    case "tool_call":
      return { who: null, text: toolCallSentence(row) };
    case "turn_ended":
      // A turn that finished or was interrupted is not news — what it said before is, and
      // the transcript already says an interrupted turn stopped. A turn that failed is the
      // news. At rest it is said as the plain reason; the backend's own words stay with
      // the failure in the opened transcript.
      return row.ending === "failed"
        ? { who: null, text: turnFailureSentence(row.errorSummary) }
        : null;
    case "turn_stopped":
      return { who: null, text: TURN_STOPPED_SENTENCE };
    case "permission_ask":
    case "user_input":
    case "plan_updated":
    case "model_changed":
    case "token_usage":
    case "context_compacted":
    case "command_reply":
      return null;
  }
}

function senderOf(senderLabel: string, ownSenderLabel: string): string {
  return promptLabelFor(senderLabel, ownSenderLabel) ?? YOU;
}

/** A tool call as the one string this line has room for — the same sentence the row in
 *  the transcript reads as. A call still running is said as it happens ("Running npm
 *  test"), beside the live mark; the past tense is for a call that has ended. */
export function toolCallSentence(row: ToolCallRow): string {
  const sentence = presentToolCall(row).sentence;
  return oneLine(sentence);
}

/** Text as one line's worth of it: the first line, and no more of that than fits. */
function oneLine(text: string): string {
  const at = text.indexOf("\n");
  const first = (at === -1 ? text : text.slice(0, at)).trim();
  return first.length <= LINE_MAXIMUM_CHARACTERS
    ? first
    : `${first.slice(0, LINE_MAXIMUM_CHARACTERS - 1).trimEnd()}…`;
}
