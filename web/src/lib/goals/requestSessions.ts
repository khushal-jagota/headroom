import type { ManualWriteBody } from './contracts';

export type ChecklistCommand = { state: 'done' | 'open' };
// A cumulative intent owns the draft before its action-local date read finishes.
// The marker is removed before the HTTP writer receives the finalized body.
export type ManualWriteCommand = ManualWriteBody & { preflight?: true };
export type RequestReceipt<C> = { command: C; status: 'pending' | 'failed'; error?: unknown };

/** A command receipt outlives a control. It never owns accepted server values. */
export class RequestSession<C> {
  current: RequestReceipt<C> | undefined;
  private pending: Promise<unknown> | undefined;
  private sequence = 0;
  private listeners = new Set<() => void>();
  constructor(private admit: () => void = () => undefined) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private notify() { for (const listener of this.listeners) listener(); }
  run<T>(command: C, operation: (prepared: (command: C) => void) => Promise<T>): Promise<T> {
    if (this.pending && this.current?.command === command) return this.pending as Promise<T>;
    // Capacity can refuse a new submission, never a render or an existing retry.
    try { this.admit(); } catch (error) { return Promise.reject(error); }
    const sequence = ++this.sequence;
    this.current = { command, status: 'pending' };
    let submitted = command;
    const prepared = (next: C) => {
      submitted = next;
      if (sequence === this.sequence) { this.current = { command: next, status: 'pending' }; this.notify(); }
    };
    let execution: Promise<T>;
    try { execution = operation(prepared); } catch (error) { execution = Promise.reject(error); }
    const attempt = execution.then(value => {
      if (sequence === this.sequence) this.current = undefined;
      return value;
    }, error => {
      if (sequence === this.sequence) this.current = { command: submitted, status: 'failed', error };
      throw error;
    }).finally(() => { if (sequence === this.sequence) { this.pending = undefined; this.notify(); } });
    this.pending = attempt;
    this.notify();
    return attempt;
  }
  discard() { if (!this.pending) { this.current = undefined; this.notify(); } }
}

const sessionLimit = 64;
export function createGoalRequestSessions(limit = sessionLimit) {
  const sessions = new Map<string, RequestSession<unknown>>();
  function makeRoom() {
    if (sessions.size < limit) return true;
    const removable = [...sessions].find(([, session]) => !session.current);
    if (!removable) return false;
    sessions.delete(removable[0]);
    return true;
  }
  function get<C>(key: string): RequestSession<C> {
    const existing = sessions.get(key);
    if (existing) { sessions.delete(key); sessions.set(key, existing); return existing as RequestSession<C>; }
    const session = new RequestSession<C>(() => {
      if (sessions.get(key) === session) return;
      if (sessions.get(key)?.current || !makeRoom()) throw new Error('Resolve or discard an existing goal change before another edit.');
      sessions.set(key, session as RequestSession<unknown>);
    });
    // Idle handles can be reclaimed. A mounted handle must regain admission on run.
    // At capacity an unregistered handle still renders normal Retry/Discard controls.
    if (makeRoom()) sessions.set(key, session as RequestSession<unknown>);
    return session;
  }
  return {
    checklist: (goalId: string, blockId: string, itemId: string) => get<ChecklistCommand>(JSON.stringify([goalId, 'checklist', blockId, itemId])),
    manual: (goalId: string, measureId: string) => get<ManualWriteCommand>(JSON.stringify([goalId, 'manual', measureId])),
    binary: (goalId: string) => get<{ completed: boolean }>(JSON.stringify([goalId, 'binary'])),
  };
}
