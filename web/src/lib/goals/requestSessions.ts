import type { ManualWriteBody } from './contracts';

export type ChecklistCommand = { state: 'done' | 'open' };
export type RequestReceipt<C> = { command: C; status: 'pending' | 'failed'; error?: unknown };

/** A command receipt outlives a control. It never owns accepted server values. */
export class RequestSession<C> {
  current: RequestReceipt<C> | undefined;
  private pending: Promise<unknown> | undefined;
  private sequence = 0;
  private listeners = new Set<() => void>();
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  private notify() { for (const listener of this.listeners) listener(); }
  run<T>(command: C, operation: () => Promise<T>): Promise<T> {
    if (this.pending && this.current?.command === command) return this.pending as Promise<T>;
    const sequence = ++this.sequence;
    this.current = { command, status: 'pending' };
    let execution: Promise<T>;
    try { execution = operation(); } catch (error) { execution = Promise.reject(error); }
    const attempt = execution.then(value => {
      if (sequence === this.sequence) this.current = undefined;
      return value;
    }, error => {
      if (sequence === this.sequence) this.current = { command, status: 'failed', error };
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
  function get<C>(key: string): RequestSession<C> {
    const existing = sessions.get(key);
    if (existing) { sessions.delete(key); sessions.set(key, existing); return existing as RequestSession<C>; }
    if (sessions.size >= limit) {
      const removable = [...sessions].find(([, session]) => !session.current);
      if (!removable) throw new Error('Resolve or discard an existing goal change before another edit.');
      sessions.delete(removable[0]);
    }
    const session = new RequestSession<C>();
    sessions.set(key, session as RequestSession<unknown>);
    return session;
  }
  return {
    checklist: (goalId: string, blockId: string, itemId: string) => get<ChecklistCommand>(JSON.stringify([goalId, 'checklist', blockId, itemId])),
    manual: (goalId: string, measureId: string) => get<ManualWriteBody>(JSON.stringify([goalId, 'manual', measureId])),
    binary: (goalId: string) => get<{ completed: boolean }>(JSON.stringify([goalId, 'binary'])),
  };
}
