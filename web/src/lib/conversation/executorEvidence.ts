import type { TicketLocale } from "../ticketMachines";
import type { ConversationView, ExecutorAskEvidence, ExecutorAskRecord } from "./wire";

/** A current pending snapshot authorizes durable acceptance for its exact callback. */
export function executorAnswerAvailable(
  view: ConversationView | null,
  kind: "permission" | "user_input",
  askId: string | undefined,
  locale: TicketLocale | undefined,
  snapshotCurrent = true
): boolean {
  if (view === null || askId === undefined) return false;
  const pending = kind === "permission" ? view.pending_permission_ask : view.pending_user_input;
  const remote = locale === "macos" || view.executor_context != null
    || pending?.answer_available !== undefined;
  if (!remote) return true;
  if (!snapshotCurrent || !view.is_running || !pending) return false;
  if (("ask_id" in pending ? pending.ask_id : pending.request_id) !== askId) return false;
  const evidence = pending.executor_evidence;
  const context = view.executor_context;
  return Boolean(
    pending.answer_available === true && evidence && context?.turn
    && (evidence.reachability === "reachable" || evidence.reachability === "uncertain") && evidence.answer_id === null
    && evidence.ask.kind === kind 
    && evidence.ask.child.conversation_id === view.conversation_id
    && evidence.ask.child.executor_incarnation === context.child.executor_incarnation
    && evidence.ask.child.child_id === context.child.child_id
    && evidence.ask.child.conversation_id === context.child.conversation_id
    && evidence.ask.turn.conversation_id === context.turn.conversation_id
    && context.turn.conversation_id === view.conversation_id
    && evidence.ask.turn.turn_number === context.turn.turn_number
  );
}

export function currentExecutorAskRecord(
  view: ConversationView | null,
  kind: "permission" | "user_input",
  askId: string | undefined
): ExecutorAskRecord | undefined {
  const context = view?.executor_context;
  const pending = kind === "permission" ? view?.pending_permission_ask : view?.pending_user_input;
  const reference = pending?.executor_evidence?.ask;
  if (!context?.turn || !pending || !reference
    || ("ask_id" in pending ? pending.ask_id : pending.request_id) !== askId) return undefined;
  return [...(view?.executor_asks ?? [])].reverse().find(({ evidence }) =>
    evidence.ask.kind === kind && evidence.ask.ask_id === reference.ask_id
    && evidence.ask.child.child_id === context.child.child_id
    && evidence.ask.child.executor_incarnation === context.child.executor_incarnation
    && evidence.ask.child.conversation_id === context.child.conversation_id
    && evidence.ask.turn.conversation_id === context.turn?.conversation_id
    && evidence.ask.turn.turn_number === context.turn?.turn_number
  );
}

/** Durable acceptance survives disconnects. Only explicit application proves delivery. */
export function executorAnswerSentence(record: ExecutorAskRecord | undefined): string | null {
  if (!record?.answer) return null;
  return record.evidence.applied_at_mac_ms !== null
    && record.evidence.answer_id === record.answer.answer_id
    ? "Answer applied on Mac."
    : record.evidence.reachability === "child_gone" || record.evidence.reachability === "callback_gone"
      ? "Answer saved on VPS; the original Mac request is gone. Application is unconfirmed."
      : "Answer saved on VPS; delivery to Mac is pending.";
}

export const EXECUTOR_ASK_UNCERTAIN = "This request is still pending. Its current availability on Mac is unknown.";

export function executorUnavailableSentence(evidence: ExecutorAskEvidence | null | undefined): string {
  if (evidence?.reachability === "child_gone") return "The Mac process for this request ended. This request cannot accept an answer.";
  if (evidence?.reachability === "callback_gone") return "The Mac callback for this request is gone. This request cannot accept an answer.";
  return EXECUTOR_ASK_UNCERTAIN;
}

/** Show receipts only for the exact current child and turn, without joining transcript IDs. */
export function currentExecutorAnswers(view: ConversationView | null): ExecutorAskRecord[] {
  const context = view?.executor_context;
  if (!context?.turn) return [];
  return (view?.executor_asks ?? []).filter(record => record.answer !== null
    && record.answer.ask.child.executor_incarnation === context.child.executor_incarnation
    && record.answer.ask.child.child_id === context.child.child_id
    && record.answer.ask.child.conversation_id === context.child.conversation_id
    && record.answer.ask.turn.conversation_id === context.turn?.conversation_id
    && record.answer.ask.turn.turn_number === context.turn?.turn_number
    && record.answer.ask.kind === record.evidence.ask.kind
    && record.answer.ask.ask_id === record.evidence.ask.ask_id
    && record.answer.ask.child.executor_incarnation === record.evidence.ask.child.executor_incarnation
    && record.answer.ask.child.child_id === record.evidence.ask.child.child_id
    && record.answer.ask.child.conversation_id === record.evidence.ask.child.conversation_id
    && record.answer.ask.turn.conversation_id === record.evidence.ask.turn.conversation_id
    && record.answer.ask.turn.turn_number === record.evidence.ask.turn.turn_number);
}
