import { writable } from "svelte/store";
import { fetchJson, PlannerFetchError } from "../api";
import { refreshScreen } from "../resourceVersions/refresh";
import { readReviewDraft, saveReviewDraft } from "./messages";
import type { SentMessagePiece } from "../conversation/wire";
import type { DecisionCommand, DecisionReceipt, SavedDecision } from "./contracts";

export type ReviewDecisionBody = Omit<DecisionCommand, "decision" | "field">;
export type ReviewDecision = { action: DecisionCommand["decision"]; body: ReviewDecisionBody };
/** A decision the server refused. The proposal it was made on shows it. */
export type DecisionRefusal = { saved: SavedDecision; error: unknown };
export const decisionRefusals = writable<DecisionRefusal[]>([]);
const prefix = "panels:decision-operation:v1:";
const refusals = new Map<string, DecisionRefusal>();
// A lookup or send that gets no answer must end, or its lock and its decision wait forever.
const requestTimeoutMs = 15_000;
export class DecisionRefusedError extends PlannerFetchError {}

export function decisionIdentity(ticketId: string, revision: number): string {
  return JSON.stringify([ticketId, revision]);
}
function storageKey(identity: string): string { return `${prefix}${identity}`; }
function storageError(): Error {
  return new Error("The previous decision cannot be recovered safely. Browser storage must be available before a decision.");
}
function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function validCommand(value: unknown): value is DecisionCommand {
  if (!isObject(value) || !["approve", "send_back"].includes(String(value.decision))
    || typeof value.field !== "string" || !value.field.trim()
    || !Number.isInteger(value.expected_proposal_revision) || Number(value.expected_proposal_revision) <= 0) return false;
  const allowed = ["decision", "field", "expected_proposal_revision", "message",
    ...(value.decision === "approve" ? ["edited_body", "next_ceiling", "next_holder"] : [])];
  if (Object.keys(value).some(key => !allowed.includes(key))) return false;
  for (const key of ["message", "edited_body", "next_ceiling"]) {
    if (key in value && value[key] !== null && typeof value[key] !== "string") return false;
  }
  return !("next_holder" in value) || value.next_holder === null || validHolder(value.next_holder);
}
function validHolder(value: unknown): boolean {
  return isObject(value) && ["owner", "chief", "manager"].includes(String(value.kind)) && typeof value.id === "string";
}
export function recallReviewDecision(identity: string): SavedDecision | null {
  try {
    // An old command has no receipt contract. Never promote it into a new operation.
    if (window.sessionStorage.getItem(`panels:review-decision:${identity}`) !== null) {
      throw new Error("A previous decision uses the older route. Open the Ticket and check it before another decision.");
    }
    const raw = window.localStorage.getItem(storageKey(identity));
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (!isObject(value) || value.version !== 1 || typeof value.ticketId !== "string"
      || typeof value.operationId !== "string" || !value.operationId || !validCommand(value.command)
      || decisionIdentity(value.ticketId, value.command.expected_proposal_revision) !== identity
      || !(value.attachmentCount === undefined || (Number.isInteger(value.attachmentCount) && Number(value.attachmentCount) > 0))) throw storageError();
    return value as SavedDecision;
  } catch (error) {
    if (error instanceof Error && error.message.includes("older route")) throw error;
    throw storageError();
  }
}
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, child: unknown) => isObject(child)
    ? Object.fromEntries(Object.keys(child).sort().map(key => [key, child[key]])) : child);
}
function save(identity: string, value: SavedDecision): void {
  try { window.localStorage.setItem(storageKey(identity), JSON.stringify(value)); }
  catch { throw storageError(); }
}
function forget(identity: string): void {
  try { window.localStorage.removeItem(storageKey(identity)); }
  catch { throw storageError(); }
}
const attachmentKey = (identity: string) => `decision:${identity}`;
/** The pictures and files saved with a decision that is not yet confirmed. */
export async function recallDecisionAttachments(identity: string): Promise<SentMessagePiece[]> {
  return (await readReviewDraft(attachmentKey(identity))) as SentMessagePiece[];
}
function publishRefusal(identity: string, saved: SavedDecision, error: unknown): void {
  refusals.set(identity, { saved, error });
  decisionRefusals.set([...refusals.values()]);
}
// A new decision on the same field replaces its earlier refusal.
function clearRefusals(ticketId: string, field: string): void {
  for (const [identity, entry] of refusals) {
    if (entry.saved.ticketId === ticketId && entry.saved.command.field === field) refusals.delete(identity);
  }
  decisionRefusals.set([...refusals.values()]);
}
export function validateDecisionResponse(value: unknown, saved: SavedDecision, allowMissing: boolean): DecisionReceipt | null {
  const invalid = () => new Error("The decision result is unconfirmed. Press the button again to check it.");
  if (!isObject(value) || value.receipt_contract !== 1) throw invalid();
  if (value.receipt === null && allowMissing) return null;
  const receipt = value.receipt;
  if (!isObject(receipt) || receipt.operation_id !== saved.operationId || receipt.ticket_id !== saved.ticketId
    || receipt.decision !== saved.command.decision || receipt.field !== saved.command.field
    || receipt.expected_proposal_revision !== saved.command.expected_proposal_revision
    || !Number.isInteger(receipt.created_at)) throw invalid();
  if (receipt.outcome === "applied") {
    const transition = receipt.transition;
    if (!isObject(transition) || receipt.error !== null || typeof transition.stage !== "string"
      || typeof transition.ceiling !== "string" || !validHolder(transition.ceiling_holder)
      || typeof transition.ticket_status !== "string" || !Number.isInteger(transition.pending_proposal_revision)) throw invalid();
  } else if (receipt.outcome === "refused") {
    if (receipt.transition !== null || !isObject(receipt.error)
      || typeof receipt.error.code !== "string" || typeof receipt.error.message !== "string"
      || !isObject(receipt.error.detail)) throw invalid();
  } else throw invalid();
  return receipt as DecisionReceipt;
}
async function locked<T>(identity: string, action: () => Promise<T>): Promise<T> {
  // The lock makes the durable admission atomic across tabs on this origin.
  if (!navigator.locks) throw new Error("This browser cannot safely coordinate decisions across tabs.");
  return navigator.locks.request(storageKey(identity), action);
}
async function commandToSend(identity: string, saved: SavedDecision): Promise<DecisionCommand> {
  if (!saved.attachmentCount) return saved.command;
  const attachments = await recallDecisionAttachments(identity);
  if (attachments.length !== saved.attachmentCount) {
    throw new Error("The pictures and files for the previous decision cannot be recovered. Open the Ticket and check it before another decision.");
  }
  return { ...saved.command, attachments };
}
async function resolve(identity: string, saved: SavedDecision): Promise<DecisionReceipt> {
  const path = `/api/tickets/${encodeURIComponent(saved.ticketId)}/decision-operations/${encodeURIComponent(saved.operationId)}`;
  // Only a validated terminal receipt clears a saved operation. A failed or timed-out request cannot.
  const found = validateDecisionResponse(await fetchJson(path, { cache: "no-store", signal: AbortSignal.timeout(requestTimeoutMs) }), saved, true);
  const receipt = found ?? validateDecisionResponse(await fetchJson(path, {
    method: "POST", body: await commandToSend(identity, saved), cache: "no-store", signal: AbortSignal.timeout(requestTimeoutMs)
  }), saved, false)!;
  forget(identity);
  if (saved.attachmentCount) await saveReviewDraft(attachmentKey(identity), []);
  const refusal = receipt.outcome === "refused"
    ? new DecisionRefusedError(
      `${saved.command.decision === "approve" ? "Approval" : "Send back"} did not apply: ${receipt.error!.message}`,
      receipt.error!.code, { detail: receipt.error!.detail }) : null;
  // Keep the refusal above proposal membership before refresh can unmount its card.
  if (refusal) publishRefusal(identity, saved, refusal);
  // The decision is terminal when the server answers. The screen catches up after it.
  void refreshScreen();
  if (refusal) throw refusal;
  return receipt;
}
export async function submitReviewDecision(ticketId: string, field: string, decision: ReviewDecision): Promise<DecisionReceipt> {
  const { attachments = [], ...body } = decision.body;
  const command = JSON.parse(JSON.stringify({ decision: decision.action, field, ...body })) as SavedDecision["command"];
  const attachmentCount = attachments.length || undefined;
  const identity = decisionIdentity(ticketId, command.expected_proposal_revision);
  return locked(identity, async () => {
    clearRefusals(ticketId, field);
    const previous = recallReviewDecision(identity);
    if (previous && (canonical(previous.command) !== canonical(command) || previous.attachmentCount !== attachmentCount)) {
      throw new Error("The previous decision is unconfirmed. Check its original note and approval choice before another decision.");
    }
    const saved: SavedDecision = previous ?? { version: 1, ticketId, operationId: crypto.randomUUID(),
      command, ...(attachmentCount ? { attachmentCount } : {}) };
    if (!previous && attachmentCount && !await saveReviewDraft(attachmentKey(identity), attachments)) {
      throw new Error("The pictures and files could not be saved in this browser. Your note is kept.");
    }
    save(identity, saved);
    return resolve(identity, saved);
  });
}
export async function recoverReviewDecision(saved: SavedDecision): Promise<void> {
  const identity = decisionIdentity(saved.ticketId, saved.command.expected_proposal_revision);
  await locked(identity, async () => {
    const current = recallReviewDecision(identity);
    if (current) await resolve(identity, current);
  });
}
/** Re-check every saved decision once, with no screen. A refusal reaches its proposal; anything else waits for the next open. */
export async function recoverSavedReviewDecisions(): Promise<void> {
  let saved: SavedDecision[];
  try { saved = savedReviewDecisions(); } catch { return; }
  await Promise.allSettled(saved.map(recoverReviewDecision));
}
export function savedReviewDecisions(): SavedDecision[] {
  try {
    const saved: SavedDecision[] = [];
    for (let index = 0; index < window.localStorage.length; index++) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith(prefix)) continue;
      const value = recallReviewDecision(key.slice(prefix.length));
      if (value) saved.push(value);
    }
    return saved;
  } catch { throw storageError(); }
}
