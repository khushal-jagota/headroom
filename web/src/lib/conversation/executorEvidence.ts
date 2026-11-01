import type { ConversationView } from "./wire";

/** A current conversation snapshot admits an answer even while the Mac is offline. */
export function executorAnswerAvailable(
  view: ConversationView | null,
  kind: "permission" | "user_input",
  askId: string | undefined,
  _locale?: string,
  snapshotCurrent = true
): boolean {
  if (!view || !askId || !snapshotCurrent || !view.is_running) return false;
  const pending = kind === "permission" ? view.pending_permission_ask : view.pending_user_input;
  return !!pending && ("ask_id" in pending ? pending.ask_id : pending.request_id) === askId;
}

export function executorAnswerSentence(fate: "saved" | "applied" | "unconfirmed" | undefined): string | null {
  if (fate === "saved") return "Answer saved on VPS; delivery to Mac is pending.";
  if (fate === "applied") return "Answer applied on Mac.";
  if (fate === "unconfirmed") return "Answer saved on VPS; the original Mac request is gone. Application is unconfirmed.";
  return null;
}

export const EXECUTOR_ASK_UNCERTAIN = "This request is still pending. Its current availability on Mac is unknown.";
