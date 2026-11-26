import type { WorkMoveItem, WorkMoveOperation, WorkMoveOwner, WorkMovePhase } from "../../../../server/work_moves/contracts";
import { fetchJson } from "../api";
import { queryClient } from "../queryClient";

/** A null owner is the separate, explicit Move everything choice. Do not wait for
 * unrelated query refetches after the server accepts this background operation. */
export async function submitWorkMove(owner: WorkMoveOwner | null, machineId: string): Promise<WorkMoveOperation> {
  const operation = await fetchJson<WorkMoveOperation>("/api/work-moves/operations", {
    method: "POST", body: owner === null ? {machine_id: machineId} : {owner, machine_id: machineId}
  });
  void queryClient.invalidateQueries();
  return operation;
}

export function sameWorkMoveOwner(left: WorkMoveOwner, right: WorkMoveOwner): boolean {
  return left.kind === right.kind && (left.kind === "chief" || (right.kind !== "chief" && left.id === right.id));
}

/** Persisted operations, including moves begun elsewhere, determine what a reopened
 * screen shows. A bulk move also supplies the progress for its individual owners. */
export function latestWorkMove(operations: readonly WorkMoveOperation[], owner: WorkMoveOwner | null, machineId?: string): WorkMoveOperation | null {
  const matches = operations.filter(operation => owner === null
    ? operation.everything && (!machineId || operation.machine_id === machineId)
    : operation.items.some(item => sameWorkMoveOwner(item.owner, owner)));
  return matches.reduce<WorkMoveOperation | null>((latest, operation) =>
    latest === null || operation.started_at_ms > latest.started_at_ms ? operation : latest, null);
}

/** One row of the dialog's phase list. `side` names the machine shown beside the row. */
export const WORK_MOVE_ROWS: readonly {label: (machine: string) => string; side: "source" | "destination"; slowAfterMs: number}[] = [
  {label: () => "Stop the agent", side: "source", slowAfterMs: 5000},
  {label: () => "Check work is pushed", side: "source", slowAfterMs: 15000},
  {label: machine => `Switch to ${machine}`, side: "destination", slowAfterMs: 3000},
  {label: () => "Prepare the workspace", side: "destination", slowAfterMs: 30000},
  {label: () => "Start the conversation", side: "destination", slowAfterMs: 15000}
];
// The slowAfterMs limits are estimates, not measurements. The server had two healthy recorded moves
// when they were set (recovery took 5.8 s and 86.1 s), too few to derive a limit from.

const ROW_OF_PHASE: Partial<Record<WorkMovePhase, number>> = {stopping: 0, verifying: 1, recording: 2, recovering: 3, starting: 4};

/** The row a recorded phase belongs to. The opening queued phase is the first row. A queued phase
 * after the others is the recovery prompt waiting at the destination, which is the last row. */
function rowOfEntry(item: WorkMoveItem, index: number): number | null {
  const phase = item.phases[index]!.phase;
  if (phase === "queued") return index === 0 ? 0 : 4;
  return ROW_OF_PHASE[phase] ?? null;
}

/** The row the server phase maps to, or null once the item is complete or failed. */
export function workMoveRowOfPhase(phase: WorkMovePhase, conversationId: string | null): number | null {
  return phase === "queued" ? (conversationId === null ? 0 : 4) : ROW_OF_PHASE[phase] ?? null;
}

/** The row that was active when the item failed. Null when it failed before any phase ran. */
export function workMoveFailedRow(item: WorkMoveItem): number | null {
  if (!item.result?.error) return null;
  const before = item.phases.length - (item.phases.at(-1)?.phase === "failed" ? 1 : 0) - 1;
  if (before < 0) return null;
  if (before === 0 && item.phases[0]!.phase === "queued") return null;
  return rowOfEntry(item, before);
}

export function workMoveReachedStopping(item: WorkMoveItem): boolean {
  return item.phases.some(phase => phase.phase === "stopping");
}

/** Bulk moves run one item after another, so an unstarted item waits its turn. */
export function workMoveWaiting(operation: WorkMoveOperation, item: WorkMoveItem): boolean {
  return item.result === null && item.phase === "queued" && item.conversation_id === null
    && operation.items.find(candidate => candidate.result === null) !== item;
}

export type WorkMoveRowState = "pending" | "active" | "done" | "failed" | "skipped";
export interface WorkMoveRowView {index: number; state: WorkMoveRowState; ms: number | null; slow: boolean}

/** Five rows for one item. Every time comes from its recorded phase times. */
export function workMoveRows(item: WorkMoveItem, now: number, waiting = false): WorkMoveRowView[] {
  const ms: (number | null)[] = WORK_MOVE_ROWS.map(() => null);
  item.phases.forEach((entry, index) => {
    const row = rowOfEntry(item, index);
    if (row === null || waiting) return;
    ms[row] = (ms[row] ?? 0) + Math.max(0, (entry.finished_at_ms ?? now) - entry.started_at_ms);
  });
  const failed = workMoveFailedRow(item);
  const current = item.result ? null : waiting ? null : workMoveRowOfPhase(item.phase, item.conversation_id);
  const unmoved = item.result !== null && item.result.error === null && !item.result.moved;
  return WORK_MOVE_ROWS.map((row, index) => {
    let state: WorkMoveRowState = "pending";
    if (item.result === null) state = current === null ? "pending" : index < current ? "done" : index === current ? "active" : "pending";
    else if (unmoved) state = "skipped";
    else if (item.result.error === null) state = "done";
    else if (failed === null) state = "skipped";
    else state = index < failed ? "done" : index === failed ? "failed" : "skipped";
    const shown = state === "done" || state === "active" || state === "failed" ? ms[index] : null;
    return {index, state, ms: shown, slow: state === "active" && shown !== null && shown > row.slowAfterMs};
  });
}

/** The time one item took: from its first phase to its last, or to now while it runs. */
export function workMoveItemMs(item: WorkMoveItem, now: number): number {
  const first = item.phases[0]?.started_at_ms ?? now;
  const last = item.phases.at(-1);
  return Math.max(0, (item.result && last ? last.finished_at_ms ?? last.started_at_ms : now) - first);
}

/** Tenths from the start: `12.3s`, then `1m 04.3s`. Never negative. */
export function workMoveDuration(ms: number): string {
  const tenths = Math.max(0, Math.floor(ms / 100));
  if (tenths < 600) return `${Math.floor(tenths / 10)}.${tenths % 10}s`;
  const rest = tenths % 600;
  return `${Math.floor(tenths / 600)}m ${String(Math.floor(rest / 10)).padStart(2, "0")}.${rest % 10}s`;
}

export type WorkMoveKind = WorkMoveOwner["kind"];
const KIND_NOUN: Record<WorkMoveKind, string> = {ticket: "Ticket", manager: "Manager", chief: "Chief"};

/** What the person does after a failure. "Stopped" is said only if the agent reached the stopping phase. */
export function workMoveNextStep(item: WorkMoveItem, kind: WorkMoveKind, sourceName: string | null): string {
  if (!item.result?.error) return "";
  if (item.result.moved) return `Nothing was lost. Fix the problem above, then choose “New conversation” on this ${KIND_NOUN[kind]}.`;
  const stopped = workMoveReachedStopping(item) ? ` The agent on ${sourceName ?? "the source machine"} is stopped.` : "";
  return `Nothing was lost.${stopped} Fix the problem above, then try again.`;
}

export interface WorkMoveSummary {ok: boolean; moved: number; attention: number; unmoved: number}
export function workMoveSummary(items: readonly WorkMoveItem[]): WorkMoveSummary {
  const results = items.map(item => item.result).filter(result => result !== null);
  const attention = results.filter(result => result.error !== null).length;
  return {ok: attention === 0, attention, moved: results.length - attention, unmoved: results.filter(result => !result.moved && result.error === null).length};
}

/** The dialog title for a finished move. */
export function workMoveFinishedTitle(items: readonly WorkMoveItem[], everything: boolean, machine: string): string {
  const summary = workMoveSummary(items);
  if (everything) return summary.ok ? `Moved ${summary.moved} to ${machine}` : `${summary.moved} moved, ${summary.attention} need attention`;
  const result = items[0]?.result;
  if (!result) return "";
  if (result.error) return result.moved ? `Moved, but ${machine} did not start` : "Not moved";
  return result.moved ? `Moved to ${machine}` : `Already on ${machine}`;
}

export function workMoveIdleTitle(kind: WorkMoveKind | null): string {
  return kind === null ? "Move everything" : kind === "chief" ? "Move the Chief" : `Move this ${KIND_NOUN[kind]}`;
}
