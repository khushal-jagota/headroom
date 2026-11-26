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
  RecordedUserInputAnswers,
  UserInputQuestion
} from "./wire";
import { messageContentOf } from "./wire";
import type { CredentialRequestRowBody } from './view/contracts';
import type { ConversationFeed } from "./feed";
import { isMessageOutcome, latestMessageOutcomes } from "./messageOutcome";

export type PermissionAskState = "live" | "answered" | "dead";
export type UserInputState = "live" | "answered" | "failed" | "dead";

/** Why a dead ask is dead. Both are the same death — its turn is gone — but they are not
 *  the same story, and a reader who was told the wrong one would go looking for a turn
 *  ending that was never written. */
export type PermissionAskDeadReason = "turn_ended" | "no_ending_recorded";

export type TranscriptRow =
  | { key: string; kind: "agent_turn_started"; sequence: number; createdAt: number; reason: string }
  | { key: string; kind: "background_task_started"; sequence: number; createdAt: number; taskDescription: string }
  | { key: string; kind: "background_task_ended"; sequence: number; createdAt: number; taskDescription: string; taskStatus: string }
  | { key: string; kind: 'credential_request'; sequence: number; createdAt: number; request: CredentialRequestRowBody }
  | {
      key: string;
      kind: "prompt";
      sequence: number;
      createdAt: number;
      content: readonly MessagePiece[];
      senderLabel: string;
      senderMessageId?: string | null;
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
      senderMessageId?: string | null;
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
      senderMessageId?: string | null;
    }
  | {
      key: string;
      kind: "prompt_discarded";
      sequence: number;
      createdAt: number;
      content: readonly MessagePiece[];
      senderLabel: string;
      senderMessageId?: string | null;
    }
  | {
      key: string;
      kind: "agent_message";
      sequence: number;
      createdAt: number;
      content: readonly MessagePiece[];
      /** The record calls this one a message to the owner rather than ordinary output.
       *  It is the only kind of row that counts as a reply somebody is owed. */
      toOwner: boolean;
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
      answerFate?: "saved" | "applied" | "unconfirmed";
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
      answers: RecordedUserInputAnswers | null;
      failureDetail: string | null;
      answerFate?: "saved" | "applied" | "unconfirmed";
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
  | { key: string; kind: "command_reply"; sequence: number; createdAt: number; replyText: string }
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
  model_resolution_failed: "the selected model is unavailable",
  backend_rejected_prompt: "the provider rejected this prompt",
  backend_did_not_start: "the agent did not start",
  session_did_not_load: "the agent could not open the conversation",
  write_to_backend_failed: "the agent did not receive it",
  no_running_turn_to_steer_into: "there was no running turn to steer into",
  backend_cannot_steer: "this agent cannot take a message during a turn",
  running_turn_changed_before_steer: "the running turn changed before delivery",
  running_turn_cannot_accept_steer: "the running turn cannot accept steering",
  message_cannot_be_steered: "this message content cannot be steered",
  backend_rejected_steer: "the agent did not accept the message during this turn",
  command_cannot_join_running_turn: "a command cannot join a turn that is already running",
  remote_steer_not_delivered: "the machine did not take this steer in time",
  refused_before_acceptance: "Panels refused this message",
  recipient_unavailable: "the recipient no longer exists",
  machine_unavailable: "its machine can no longer join",
  conversation_save_failed: "the conversation could not save",
  home_restarted_before_acceptance: "Panels restarted before it accepted this message",
  recipient_conversation_replaced: "the recipient's conversation was replaced"
};

/** Legacy browser drafts can still hold a full failure label and the older reason. */
export function messageNotDelivered(reason: string): string {
  const plain = reason.trim().replace(/^(?:not delivered\b[\s·:.]*)+/i, "").trim()
    .replace(/^the write to the backend did not succeed[.!]?$/i, "The agent did not receive it")
    .replace(/^the backend did not start[.!]?$/i, "The agent did not start");
  if (!plain) return "Not delivered. The agent did not receive it.";
  const sentence = plain.charAt(0).toUpperCase() + plain.slice(1);
  return `Not delivered. ${sentence}${/[.!?]$/.test(sentence) ? "" : "."}`;
}

export function refusalSentence(reason: PromptDeliveryRefusalReason, detail?: string | null): string {
  return detail?.trim() || REFUSAL_SENTENCES[reason] || "the agent could not receive the message";
}

/** What a message that was taken back before anything received it says about itself. The
 *  thread says it beside the message, and the rest line says it on its own. */
export const PROMPT_DISCARDED_SENTENCE = "discarded without being delivered";
export const AUTOMATIC_COMPACTION_NOT_CONFIRMED_SENTENCE = "context was not compacted";

/** A line in the thread that says what happened to a turn or a message, rather than
 *  anything anybody said. Every one of them is drawn the same way: a mark in its tone, a
 *  plain sentence a person can act on, and, when the backend said something more exact,
 *  that exact text beneath it, quieter, so nothing it said is lost.
 *
 *  `stopped` is a turn that ended before it finished, by the person's hand or with no
 *  ending recorded: not a problem, but more than a turn's ordinary metadata. */
export type TranscriptStatus = {
  tone: "error" | "warning" | "stopped" | "neutral";
  title: string;
  detail: string | null;
  /** What the person can do now, in plain words, when the product knows a real next step.
   *  It is said, not offered as a control: only things the person can actually do. */
  next?: string | null;
  /** v9 (7): the card's words when its one button says the rest (a rate limit: "The
   *  model's rate limit was reached. It resets in 41m."). */
  brief?: string | null;
  /** Whether the backend's own words are shown open. They are when the sentence above
   *  could not name a cause, so the words are the only explanation there is. */
  detailOpen?: boolean;
  /** The one real next step the status offers as a control, when there is one the person
   *  can take from the thread. "choose-model" opens the composer's model picker. */
  action?: "choose-model" | null;
};

/** The plain reason a turn failed, read from the backend's own words, and what the person
 *  can do about it. Only causes those words name are said; anything else is said as an
 *  error, and the words stay beneath. */
const TURN_FAILURE_CAUSES: readonly [RegExp, string, (said: string) => string, ("choose-model" | null)?][] = [
  [/rate[ _-]?limit|usage limit|quota|too many requests|\b429\b/i, "The turn stopped because the model's rate limit was reached.", rateLimitNextStep, "choose-model"],
  [/unauthori[sz]ed|authenticat|\b401\b|\bapi key\b|not signed in|sign[ -]?in/i, "The turn stopped because the provider would not accept the sign-in.", () => "Sign in to the provider again, then send a message."],
  [/disconnect|connection (?:reset|closed|refused|lost)|network|socket hang up|econnreset|timed out|timeout/i, "The turn stopped because the connection to the model dropped.", () => "Send a message to try again."],
  [/exited with code|exited unexpectedly|crashed|was killed|\bsignal\b|sigkill|sigterm/i, "The turn stopped because the agent's process exited.", () => "Send a message to start it again."]
];
const TURN_FAILURE_UNNAMED = "The turn stopped because of an error.";

/** When a limit lifts is the one next step a rate limit has. The provider says it relative
 *  to the failure ("resets in 41m"), so it is quoted as the provider's, never restated as
 *  a countdown from now. */
function rateLimitResetsWhen(said: string): string | null {
  return /\bresets? ((?:in|at) [^()\n;,]+?)\s*(?:\)|$|[;,\n]|\.\s)/i.exec(said)?.[1]?.trim() ?? null;
}
function rateLimitNextStep(said: string): string {
  const when = rateLimitResetsWhen(said);
  return when
    ? `The provider said the limit resets ${when}. Send a message after that, or choose another model.`
    : "Send a message once the limit resets, or choose another model.";
}

/** v9 (7): a rate-limited turn's card says the cause and when it lifts; its button
 *  ("Choose another model") and Details say the rest. */
export function rateLimitBrief(said: string): string {
  const when = rateLimitResetsWhen(said);
  return when ? `The model's rate limit was reached. It resets ${when}.` : "The model's rate limit was reached.";
}

export function turnFailureSentence(errorSummary: string | null): string {
  const said = errorSummary?.trim() ?? "";
  for (const [pattern, sentence] of TURN_FAILURE_CAUSES) if (pattern.test(said)) return sentence;
  return TURN_FAILURE_UNNAMED;
}

/** How a turn's ending is drawn in the thread, or null when it draws nothing. */
export function turnEndingStatus(
  ending: ConversationTurnEnding,
  errorSummary: string | null,
  /** How long the turn ran, when its head is known. A stopped turn then says it in its one
   *  line ("Stopped after 8m 45s"), and no head says it again above. */
  durationSeconds: number | null = null
): TranscriptStatus | null {
  if (ending === "failed") {
    const said = errorSummary?.trim() || null;
    const cause = TURN_FAILURE_CAUSES.find(([pattern]) => pattern.test(said ?? ""));
    return {
      tone: "error",
      title: cause?.[1] ?? TURN_FAILURE_UNNAMED,
      detail: said,
      next: cause ? cause[2](said ?? "") : "Send a message to try again.",
      action: cause?.[3] ?? null,
      brief: cause?.[3] === "choose-model" ? rateLimitBrief(said ?? "") : null,
      detailOpen: cause === undefined
    };
  }
  if (ending === "interrupted") {
    return { tone: "stopped", title: durationSeconds !== null && durationSeconds > 0 ? `Stopped after ${formatDuration(durationSeconds)}` : "Stopped before it finished", detail: null };
  }
  return null;
}

/** A status's reason in one short phrase, for the line "This turn failed · <reason>" or
 *  "Not delivered · <reason>": the brief when there is one, otherwise the cause the title
 *  names, without its lead-in and its full stop. */
export function statusReason(status: Pick<TranscriptStatus, "title" | "brief">): string {
  const said = (status.brief ?? status.title)
    .replace(/^Not delivered[.:]\s*/, "")
    .replace(/^The turn stopped because\s*/, "")
    .replace(/^of\s+/, "")
    .trim()
    .replace(/[.!?]$/, "");
  return said.charAt(0).toUpperCase() + said.slice(1);
}

/** A turn the record never wrote an ending for, in the same family. */
export const TURN_STOPPED_STATUS: TranscriptStatus = {
  tone: "stopped",
  title: "Stopped before it finished",
  detail: null
};

/** What the person can do about a message that was turned away, by why it was. A reason
 *  with nothing the person can do about it says nothing more. */
const STEER_REFUSED_NEXT_STEP = "Send it again with Queue, so it goes in after this turn.";
const REFUSAL_NEXT_STEPS: Partial<Record<PromptDeliveryRefusalReason, string>> = {
  model_resolution_failed: "Choose another model in the composer, then send it again.",
  backend_rejected_prompt: "Change the message, then send it again.",
  backend_did_not_start: "Send it again to retry, or choose another model in the composer.",
  session_did_not_load: "Send it again to retry.",
  write_to_backend_failed: "Send it again to retry.",
  no_running_turn_to_steer_into: "The turn had already ended. Send it again to start a new one.",
  backend_cannot_steer: STEER_REFUSED_NEXT_STEP,
  running_turn_changed_before_steer: STEER_REFUSED_NEXT_STEP,
  running_turn_cannot_accept_steer: STEER_REFUSED_NEXT_STEP,
  message_cannot_be_steered: STEER_REFUSED_NEXT_STEP,
  backend_rejected_steer: STEER_REFUSED_NEXT_STEP,
  remote_steer_not_delivered: STEER_REFUSED_NEXT_STEP,
  command_cannot_join_running_turn: "Send it again after this turn ends."
};

/** The refusals that sending the same message again can get past: the backend or the
 *  moment was the problem, not the message or the conversation. Under these the message
 *  offers Resend, and its next step names that button. */
const WHILE_A_TURN_RUNS = "Try again. While a turn is running, it waits for that turn to end.";
const RESEND_NEXT_STEPS: Partial<Record<PromptDeliveryRefusalReason, string>> = {
  model_resolution_failed: "Choose another model in the composer, then try again.",
  backend_did_not_start: "Try again, or choose another model in the composer first.",
  session_did_not_load: "Try again to retry.",
  write_to_backend_failed: "Try again to retry.",
  no_running_turn_to_steer_into: "The turn had already ended. Try again to start a new one.",
  backend_cannot_steer: WHILE_A_TURN_RUNS,
  running_turn_changed_before_steer: WHILE_A_TURN_RUNS,
  running_turn_cannot_accept_steer: WHILE_A_TURN_RUNS,
  message_cannot_be_steered: WHILE_A_TURN_RUNS,
  backend_rejected_steer: WHILE_A_TURN_RUNS,
  remote_steer_not_delivered: WHILE_A_TURN_RUNS,
  command_cannot_join_running_turn: WHILE_A_TURN_RUNS
};

/** Whether a message refused for this reason offers Resend. */
export function refusalOffersResend(reason: PromptDeliveryRefusalReason): boolean {
  return RESEND_NEXT_STEPS[reason] !== undefined;
}

/** What became of a message the person sent, under the message rather than inside it: the
 *  failure is the system's, and the words above it are still theirs. A refused message
 *  that offers Resend says so in its next step; one that cannot (its reason, or an
 *  attachment that cannot go out again) keeps the step that asks the person to send it. */
export function promptOutcomeStatus(
  row: { kind: "prompt_refused"; reason: PromptDeliveryRefusalReason; sentence: string } | { kind: "prompt_uncertain" | "prompt_discarded" },
  resendable = false
): TranscriptStatus {
  if (row.kind !== "prompt_refused") {
    return row.kind === "prompt_uncertain"
      ? { tone: "warning", title: "The agent did not confirm receipt.", detail: null }
      : { tone: "neutral", title: "Discarded without being delivered", detail: null };
  }
  const plain = REFUSAL_SENTENCES[row.reason] ?? "the agent could not receive the message";
  return {
    tone: "error",
    title: messageNotDelivered(plain),
    detail: row.sentence.trim() !== "" && row.sentence !== plain ? row.sentence : null,
    next: (resendable ? RESEND_NEXT_STEPS[row.reason] : undefined) ?? REFUSAL_NEXT_STEPS[row.reason] ?? null,
    // Where the next step names another model, the callout offers it as a control.
    action: row.reason === "model_resolution_failed" || row.reason === "backend_did_not_start" ? "choose-model" : null
  };
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
  const credentialRequestRowIndex = new Map<string, number>();
  // Half-finished output belongs to a turn that is running. When the turn is gone,
  // whatever was left in flight is not arriving, and it is not drawn.
  const turnIsGone = reading.turnStoppedWithoutAnEnding === true;

  const latestOutcomes = latestMessageOutcomes(feed.events);
  const reconciledSequences = new Set(feed.events.flatMap((event) =>
    event.kind === "prompt" && event.payload.reconciles_sequence !== undefined
      && event.payload.reconciles_sequence < event.sequence
      ? [event.payload.reconciles_sequence] : []
  ));
  for (const event of feed.events) {
    if (event.kind === "prompt_delivery_uncertain" && reconciledSequences.has(event.sequence)) continue;
    if (isMessageOutcome(event) && event.payload.sender_message_id !== undefined
      && latestOutcomes.get(event.payload.sender_message_id)?.sequence !== event.sequence) continue;
    const sequence = event.sequence;
    const createdAt = event.created_at;
    switch (event.kind) {
      case 'credential_request': {
        const request = event.payload.request;
        const previous = credentialRequestRowIndex.get(request.request_id);
        if (previous === undefined) {
          credentialRequestRowIndex.set(request.request_id, rows.length);
          rows.push({ key: `e${sequence}`, kind: 'credential_request', sequence, createdAt, request });
        } else {
          const row = rows[previous];
          if (row?.kind === 'credential_request') rows[previous] = { ...row, request };
        }
        break;
      }
      case "agent_turn_started":
        rows.push({ key: `e${sequence}`, kind: event.kind, sequence, createdAt, reason: event.payload.reason });
        break;
      case "background_task_started":
        rows.push({ key: `e${sequence}`, kind: event.kind, sequence, createdAt, taskDescription: event.payload.task_description });
        break;
      case "background_task_ended":
        rows.push({ key: `e${sequence}`, kind: event.kind, sequence, createdAt, taskDescription: event.payload.task_description, taskStatus: event.payload.task_status });
        break;
      case "prompt":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt",
          sequence,
          createdAt,
          content: messageContentOf(event.payload),
          senderLabel: event.payload.sender_label,
          senderMessageId: event.payload.sender_message_id ?? null,
          mode: event.payload.mode,
          sentAtUnixMilliseconds: sentAtUnixMilliseconds(event.payload, createdAt),
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
          senderMessageId: event.payload.sender_message_id ?? null,
          reason: event.payload.refusal_reason,
          sentence: refusalSentence(event.payload.refusal_reason, event.payload.refusal_detail),
        });
        break;
      case "prompt_delivery_uncertain":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt_uncertain",
          sequence,
          createdAt,
          content: messageContentOf(event.payload),
          senderLabel: event.payload.sender_label,
          senderMessageId: event.payload.sender_message_id ?? null
        });
        break;
      case "prompt_discarded":
        rows.push({
          key: `e${sequence}`,
          kind: "prompt_discarded",
          sequence,
          createdAt,
          content: messageContentOf(event.payload),
          senderLabel: event.payload.sender_label,
          senderMessageId: event.payload.sender_message_id ?? null
        });
        break;
      case "agent_message":
      case "message_to_owner":
        rows.push({
          key: `e${sequence}`,
          kind: "agent_message",
          sequence,
          createdAt,
          content: messageContentOf(event.payload),
          toOwner: event.kind === "message_to_owner"
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
      case "command_reply":
        rows.push({
          key: `e${sequence}`,
          kind: "command_reply",
          sequence,
          createdAt,
          replyText: event.payload.reply_text
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

/** A length of time, in the one format this pane says them in.
 *
 * Whole seconds throughout: the record keeps integer unix seconds, so a tenth of a second
 * is not a thing anybody here can know. Under a minute is just seconds; under an hour it is
 * minutes and seconds, with the seconds left off when there are none; from an hour it is
 * hours and minutes.
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder === 0 ? `${minutes}m` : `${minutes}m ${remainder}s`;
}

/** How long a turn's work took. A turn too short to measure claims no length at all. */
export function workedSentence(durationSeconds: number | null): string {
  if (durationSeconds === null || durationSeconds <= 0) return "Worked";
  return `Worked for ${formatDuration(durationSeconds)}`;
}

/** Background work the agent started that outlived its turn, named by its own description. */
export function backgroundWorkSentence(taskDescriptions: readonly string[]): string {
  return `Background work running: ${taskDescriptions.join(", ")}`;
}

/** The background tasks a conversation lists: none while a turn runs or activity is unknown. */
export function backgroundTasksAfterTheTurn<T>(
  status: { turn_running: boolean | null; background_tasks: readonly T[] } | null | undefined
): readonly T[] {
  return status?.turn_running === false ? status.background_tasks : [];
}

/** A background task the agent started is still live after its turn ended. The conversation
 *  counts it as working, like the sidebar. It is false while a turn runs or activity is unknown. */
export function hasBackgroundTaskAfterTheTurn(
  status: { turn_running: boolean | null; background_tasks: readonly unknown[] } | null | undefined
): boolean {
  return backgroundTasksAfterTheTurn(status).length > 0;
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

/** What a settled turn's fold holds in Focus: only what the turn said on the way to its
 *  answer. Focus never counts or opens tool calls; that work belongs to Full. */
export function earlierMessagesSentence(messageCount: number): string {
  return messageCount <= 0 ? "" : `${messageCount} earlier message${messageCount === 1 ? "" : "s"}`;
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
export const CONTEXT_COMPACTED_SENTENCE = "Context compacted";

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

/** The words of a message, as a copy of it should hold them. Pictures and files are not
 *  words, so a message that is only those has nothing to copy and offers no copy. */
export function messageCopyText(content: readonly { piece: string; text?: string }[]): string {
  return content.flatMap(piece => piece.piece === "text" && typeof piece.text === "string" && piece.text.trim() ? [piece.text] : []).join("\n\n");
}

/** When a message was recorded, read the way a thread reads it: the time alone today,
 *  the day and time before that. Null when the record holds no time. */
export function messageTimeLabel(createdAtUnixSeconds: number | null | undefined, now: Date = new Date()): { short: string; full: string; iso: string } | null {
  if (typeof createdAtUnixSeconds !== "number" || !Number.isFinite(createdAtUnixSeconds) || createdAtUnixSeconds <= 0) return null;
  const at = new Date(createdAtUnixSeconds * 1000);
  const time = at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  const sameDay = at.getFullYear() === now.getFullYear() && at.getMonth() === now.getMonth() && at.getDate() === now.getDate();
  const day = at.toLocaleDateString(undefined, { month: "short", day: "numeric", ...(at.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }) });
  return {
    short: sameDay ? time : `${day}, ${time}`,
    full: at.toLocaleString(undefined, { dateStyle: "full", timeStyle: "short" }),
    iso: at.toISOString()
  };
}

/** What the ask card above the composer is saying right now, as the transcript needs to
 *  know it. Given by the live conversation; absent, the card is taken to say nothing. */
export const ASK_CARD_CONTEXT = Symbol("conversation-ask-card");
export type AskCardReading = {
  /** Whether the card for this ask is showing that its availability on the machine is unknown. */
  saysAvailabilityUnknown(kind: "permission" | "user_input", id: string): boolean;
};

/** A live ask nobody has answered yet is shown once, by its card above the composer, and
 *  the turn head already says it waits. Its transcript row is the record, drawn once the
 *  ask is answered, saved, failed or dead. While the machine's activity is unknown the row
 *  stays, because it says so, unless the card is already saying the same. */
export function askShownByItsCard(
  row: { state: string; answerFate?: string | null },
  activityUncertain = false,
  cardSaysAvailabilityUnknown = false
): boolean {
  return row.state === "live" && !row.answerFate && (!activityUncertain || cardSaysAvailabilityUnknown);
}
