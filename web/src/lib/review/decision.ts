import { writable } from "svelte/store";
import { fetchJson, PlannerFetchError } from "../api";
import { queryClient } from "../queryClient";
import type { DecisionCommand, DecisionReceipt, SavedDecision } from "./contracts";

export type ReviewDecisionBody = Omit<DecisionCommand, "decision" | "field">;
export type ReviewDecision = { action: DecisionCommand["decision"]; body: ReviewDecisionBody };
export type DecisionRecovery = { saved: SavedDecision; error: unknown; busy: boolean; terminal?: boolean };
export const decisionRecovery = writable<DecisionRecovery[]>([]);
const prefix = "panels:decision-operation:v1:";
const active = new Map<string, DecisionRecovery>();
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
      throw new Error("A previous decision uses the older route. Check the Ticket before another decision.");
    }
    const raw = window.localStorage.getItem(storageKey(identity));
    if (raw === null) return null;
    const value: unknown = JSON.parse(raw);
    if (!isObject(value) || value.version !== 1 || typeof value.ticketId !== "string"
      || typeof value.operationId !== "string" || !value.operationId || !validCommand(value.command)
      || decisionIdentity(value.ticketId, value.command.expected_proposal_revision) !== identity) throw storageError();
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
function publish(identity: string, saved: SavedDecision, error: unknown, busy: boolean, terminal = false): void {
  active.set(identity, { saved, error, busy, terminal });
  decisionRecovery.set([...active.values()]);
}
function forget(identity: string): void {
  try { window.localStorage.removeItem(storageKey(identity)); }
  catch { throw storageError(); }
  active.delete(identity);
  decisionRecovery.set([...active.values()]);
}
export function dismissDecisionRecovery(saved: SavedDecision): void {
  active.delete(decisionIdentity(saved.ticketId, saved.command.expected_proposal_revision));
  decisionRecovery.set([...active.values()]);
}
export function validateDecisionResponse(value: unknown, saved: SavedDecision, allowMissing: boolean): DecisionReceipt | null {
  const invalid = () => new Error("The decision result is unconfirmed. Check the decision again.");
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
async function resolve(identity: string, saved: SavedDecision): Promise<DecisionReceipt> {
  publish(identity, saved, null, true);
  try {
    const path = `/api/tickets/${encodeURIComponent(saved.ticketId)}/decision-operations/${encodeURIComponent(saved.operationId)}`;
    const found = validateDecisionResponse(await fetchJson(path, { cache: "no-store" }), saved, true);
    const receipt = found ?? validateDecisionResponse(await fetchJson(path, {
      method: "POST", body: saved.command, cache: "no-store"
    }), saved, false)!;
    forget(identity);
    const refusal = receipt.outcome === "refused"
      ? new DecisionRefusedError(receipt.error!.message, receipt.error!.code, { detail: receipt.error!.detail }) : null;
    // Keep the refusal above proposal membership before refresh can unmount its card.
    if (refusal) publish(identity, saved, refusal, false, true);
    // A refresh failure cannot undo a confirmed terminal decision.
    await queryClient.invalidateQueries().catch(() => undefined);
    if (refusal) throw refusal;
    return receipt;
  } catch (error) {
    // Only a validated terminal receipt can clear an operation. HTTP failures cannot.
    if (recallReviewDecision(identity)) publish(identity, saved, error, false);
    throw error;
  }
}
export async function submitReviewDecision(ticketId: string, field: string, decision: ReviewDecision): Promise<DecisionReceipt> {
  const command = JSON.parse(JSON.stringify({ decision: decision.action, field, ...decision.body })) as DecisionCommand;
  const identity = decisionIdentity(ticketId, command.expected_proposal_revision);
  return locked(identity, async () => {
    const previous = recallReviewDecision(identity);
    if (previous && canonical(previous.command) !== canonical(command)) {
      throw new Error("The previous decision is unconfirmed. Check its original note and approval choice before another decision.");
    }
    const saved: SavedDecision = previous ?? { version: 1, ticketId, operationId: crypto.randomUUID(),
      command };
    save(identity, saved);
    return resolve(identity, saved);
  });
}
export async function recoverReviewDecision(saved: SavedDecision): Promise<void> {
  const identity = decisionIdentity(saved.ticketId, saved.command.expected_proposal_revision);
  await locked(identity, async () => {
    const current = recallReviewDecision(identity);
    if (current) await resolve(identity, current);
    else { active.delete(identity); decisionRecovery.set([...active.values()]); }
  });
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
