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
  AutomaticCompactionResult,
  ConversationTurnEnding,
  MessagePiece,
  PermissionAskOption,
  PlanEntry,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason,
  Principal,
  UserInputAnswers,
  UserInputQuestion
} from "./wire";
import { messageContentOf } from "./wire";
import type { ConversationFeed } from "./feed";

export type PermissionAskState = "live" | "answered" | "dead";
export type UserInputState = "live" | "answered" | "failed" | "dead";

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
      content: readonly MessagePiece[];
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
      content: readonly MessagePiece[];
      senderLabel: string;
      reason: PromptDeliveryRefusalReason;
      sentence: string;
    }
  | {
      key: string;
      kind: "prompt_uncertain";
      sequence: number;
      createdAt: number;
      content: readonly MessagePiece[];
      senderLabel: string;
    }
  | {
      key: string;
      kind: "proposal_delivery_failed";
      sequence: number;
      createdAt: number;
      attemptCount: number;
      lastError: string;
    }
  | {
      key: string;
      kind: "prompt_discarded";
      sequence: number;
      createdAt: number;
      content: readonly MessagePiece[];
      senderLabel: string;
    }
  | {
      key: string;
      kind: "agent_message";
      sequence: number;
      createdAt: number;
      content: readonly MessagePiece[];
    }
  | {
      key: string;
      kind: "explicit_reply_missing";
      sequence: number;
      createdAt: number;
      promptSender: Principal;
    }
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
      /** Where the whole output is, when `detail` holds only the start of it. The finish
       *  row's own position, which is not this row's: a finish is drawn into the line its
       *  start opened, and that line keeps the start's sequence. Null means `detail` is
       *  the whole of what the tool printed. */
      cappedDetailSequence: number | null;
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
      kind: "user_input";
      sequence: number;
      createdAt: number;
      requestId: string;
      questions: readonly UserInputQuestion[];
      state: UserInputState;
      deadReason: PermissionAskDeadReason | null;
      answers: UserInputAnswers | null;
      failureDetail: string | null;
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
      kind: "token_usage";
      sequence: number;
      createdAt: number;
      inputTokens: number | null;
      outputTokens: number | null;
      cachedInputTokens: number | null;
      costUsd: number | null;
    }
  /** Where the backend cut the thread. Drawn as the seam it is, so a reader knows why
   *  what came before has gone rather than thinking the agent forgot. */
  | { key: string; kind: "context_compacted"; sequence: number; createdAt: number }
  | {
      key: string;
      kind: "turn_ended";
      sequence: number;
      createdAt: number;
      ending: ConversationTurnEnding;
      errorSummary: string | null;
      automaticCompactionResult: AutomaticCompactionResult | null;
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
  backend_cannot_steer: "this backend cannot take text into a running turn",
  running_turn_changed_before_steer: "the running turn changed before delivery",
  running_turn_cannot_accept_steer: "the running turn cannot accept steering",
  message_cannot_be_steered: "this message content cannot be steered",
  backend_rejected_steer: "the backend rejected steering for this turn"
};

export function refusalSentence(reason: PromptDeliveryRefusalReason): string {
  return REFUSAL_SENTENCES[reason] ?? "the delivery was impossible";
}

/** What a message that was taken back before anything received it says about itself. The
 *  thread says it beside the message, and the rest line says it on its own. */
export const PROMPT_DISCARDED_SENTENCE = "discarded without being delivered";
export const PROPOSAL_DELIVERY_FAILED_SENTENCE = "proposal alert could not reach its holder";
export const AUTOMATIC_COMPACTION_NOT_CONFIRMED_SENTENCE = "context was not compacted";

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

function killOpenUserInputs(
  rows: TranscriptRow[],
  rowIndex: Map<string, number>,
  reason: PermissionAskDeadReason
): void {
  for (const at of rowIndex.values()) {
    const requested = rows[at];
    if (requested?.kind === "user_input" && requested.state === "live") {
      rows[at] = { ...requested, state: "dead", deadReason: reason };
    }
  }
  rowIndex.clear();
}

export function transcriptRows(
  feed: ConversationFeed,
  reading: TranscriptReading = {}
): TranscriptRow[] {
  const rows: TranscriptRow[] = [];
  const toolCallRowIndex = new Map<string, number>();
  const askRowIndex = new Map<string, number>();
  const userInputRowIndex = new Map<string, number>();
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
          content: messageContentOf(event.payload),
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
          content: messageContentOf(event.payload),
          senderLabel: event.payload.sender_label,
          reason: event.payload.refusal_reason,
          sentence: refusalSentence(event.payload.refusal_reason)
        });
        break;
      case "prompt_delivery_uncertain":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt_uncertain",
          sequence,
          createdAt,
          content: messageContentOf(event.payload),
          senderLabel: event.payload.sender_label
        });
        break;
      case "proposal_delivery_failed":
        rows.push({
          key: `e${sequence}`,
          kind: "proposal_delivery_failed",
          sequence,
          createdAt,
          attemptCount: event.payload.attempt_count,
          lastError: event.payload.last_error
        });
        break;
      case "prompt_discarded":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt_discarded",
          sequence,
          createdAt,
          content: messageContentOf(event.payload),
          senderLabel: event.payload.sender_label
        });
        break;
      case "agent_message":
      case "message_to_owner":
        rows.push({
          key: `e${sequence}`,
          kind: "agent_message",
          sequence,
          createdAt,
          content: messageContentOf(event.payload)
        });
        break;
      case "explicit_reply_missing":
        rows.push({
          key: `e${sequence}`,
          kind: "explicit_reply_missing",
          sequence,
          createdAt,
          promptSender: event.payload.prompt_sender
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
          cappedDetailSequence: null,
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
            cappedDetailSequence: event.payload.detail_capped === true ? sequence : null,
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
          cappedDetailSequence: event.payload.detail_capped === true ? sequence : null,
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
      case "user_input_requested":
        userInputRowIndex.set(event.payload.request_id, rows.length);
        rows.push({
          key: `e${sequence}`,
          kind: "user_input",
          sequence,
          createdAt,
          requestId: event.payload.request_id,
          questions: event.payload.questions,
          state: "live",
          deadReason: null,
          answers: null,
          failureDetail: null
        });
        break;
      case "user_input_answered": {
        const at = userInputRowIndex.get(event.payload.request_id);
        const requested = at === undefined ? undefined : rows[at];
        if (at !== undefined && requested?.kind === "user_input") {
          rows[at] = {
            ...requested,
            state: "answered",
            answers: event.payload.answers
          };
          userInputRowIndex.delete(event.payload.request_id);
        }
        break;
      }
      case "user_input_failed": {
        const at = userInputRowIndex.get(event.payload.request_id);
        const requested = at === undefined ? undefined : rows[at];
        if (at !== undefined && requested?.kind === "user_input") {
          rows[at] = {
            ...requested,
            state: "failed",
            failureDetail: event.payload.detail
          };
          userInputRowIndex.delete(event.payload.request_id);
        } else {
          // A malformed backend payload cannot safely become a request, so its failure
          // may be the only row there is. It must still be visible rather than vanishing
          // merely because there was deliberately no interactive request before it.
          rows.push({
            key: `e${sequence}`,
            kind: "user_input",
            sequence,
            createdAt,
            requestId: event.payload.request_id,
            questions: [],
            state: "failed",
            deadReason: null,
            answers: null,
            failureDetail: event.payload.detail
          });
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
      case "token_usage":
        rows.push({
          key: `e${sequence}`,
          kind: "token_usage",
          sequence,
          createdAt,
          inputTokens: event.payload.input_tokens ?? null,
          outputTokens: event.payload.output_tokens ?? null,
          cachedInputTokens: event.payload.cached_input_tokens ?? null,
          costUsd: event.payload.cost_usd ?? null
        });
        break;
      case "context_compacted":
        rows.push({
          key: `e${sequence}`,
          kind: "context_compacted",
          sequence,
          createdAt
        });
        break;
      case "turn_ended":
        // Every ask still open belonged to the turn that just ended, so it ended too.
        killOpenAsks(rows, askRowIndex, "turn_ended");
        killOpenUserInputs(rows, userInputRowIndex, "turn_ended");
        rows.push({
          key: `e${sequence}`,
          kind: "turn_ended",
          sequence,
          createdAt,
          ending: event.payload.ending,
          errorSummary: event.payload.error_summary,
          automaticCompactionResult: event.payload.automatic_compaction_result ?? null
        });
        break;
    }
  }

  if (turnIsGone) {
    // The turn is gone and its ending was never written, so the asks that were waiting
    // on it are as dead as any other — they just have a different story.
    killOpenAsks(rows, askRowIndex, "no_ending_recorded");
    killOpenUserInputs(rows, userInputRowIndex, "no_ending_recorded");
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

export function principalLabel(principal: Principal): string {
  switch (principal.kind) {
    case "owner": return "owner";
    case "chief": return "Chief";
    case "ticket": return `Ticket ${principal.id}`;
    case "sprint_item": return `Sprint Item ${principal.id}`;
  }
}

export function explicitReplyMissingSentence(principal: Principal): string {
  return `No explicit message was sent to ${principalLabel(principal)}.`;
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
  payload: { sent_at_unix_milliseconds?: number },
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

/** How many of a running turn's tool calls stay visible in each run. The newest one is
 *  what is happening; the ones before it are what happened, and they wait behind a count. */
export const VISIBLE_RUNNING_WORK_ENTRIES = 1;

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

/** How far through its plan the conversation is: what it has finished, out of what it
 *  said it would do.
 *
 * Two places read this — the strip under a turn's head, and the line above the composer
 * when the conversation is shut — and they are two views of one conversation's one plan.
 * A person moving between them is watching the same thing from further away, so the
 * number and the words have to be the same number and the same words.
 */
export function planProgressSentence(entries: readonly PlanEntry[]): string {
  return `${completedPlanEntryCount(entries)} / ${entries.length} tasks`;
}

/** The same fact for anybody hearing the page rather than seeing it. A count written as
 *  a fraction is read out as one, and "one slash three tasks" is not a sentence. */
export function planProgressSpokenSentence(entries: readonly PlanEntry[]): string {
  return `${completedPlanEntryCount(entries)} of ${entries.length} tasks complete`;
}

function completedPlanEntryCount(entries: readonly PlanEntry[]): number {
  return entries.filter((entry) => entry.status === "completed").length;
}

/** What the seam where the backend cut the thread says. */
export const CONTEXT_COMPACTED_SENTENCE = "context compacted";

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

/** The structured question request currently waiting, reconstructed from durable rows. */
export function liveUserInputFrom(rows: readonly TranscriptRow[]): Extract<
  TranscriptRow,
  { kind: "user_input" }
> | null {
  for (let at = rows.length - 1; at >= 0; at -= 1) {
    const row = rows[at];
    if (row?.kind === "user_input" && row.state === "live") return row;
  }
  return null;
}
