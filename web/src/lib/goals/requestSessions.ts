import type { ManualWriteBody } from './contracts';

export type ChecklistCommand = { state: 'done' | 'open' };
export type ChecklistDeadlineCommand = { deadline: string | null; revision: number };
export type ChecklistDeadlineDraft = {
  value: string;
  initialValue: string;
  revision: number;
  error: unknown;
  dirty: boolean;
};
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
  constructor(protected admit: () => void = () => undefined) {}
  get retained() { return Boolean(this.current); }
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  protected notify() { for (const listener of this.listeners) listener(); }
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

/** Native validation can fail before a request exists. Keep that draft with its owner. */
export class ChecklistDeadlineSession extends RequestSession<ChecklistDeadlineCommand> {
  draft: ChecklistDeadlineDraft | undefined;
  override get retained() { return super.retained || Boolean(this.draft?.dirty || this.draft?.error); }
  begin(value: string, revision: number) {
    this.admit();
    if (this.draft || this.current) return;
    this.draft = { value, initialValue: value, revision, error: null, dirty: false };
    this.notify();
  }
  update(value: string, error: unknown = null) {
    this.admit();
    if (!this.draft) throw new Error('Focus this date before another edit.');
    this.draft = { ...this.draft, value, error, dirty: value !== this.draft.initialValue || Boolean(error) };
    this.notify();
  }
  override run<T>(command: ChecklistDeadlineCommand, operation: (prepared: (command: ChecklistDeadlineCommand) => void) => Promise<T>): Promise<T> {
    const submittedDraft = this.draft;
    return super.run(command, operation).then(value => {
      // A newer draft belongs to its own edit, even if an older request finishes late.
      if (!this.current && this.draft === submittedDraft) { this.draft = undefined; this.notify(); }
      return value;
    });
  }
  override discard() {
    if (this.current?.status === 'pending') return;
    this.draft = undefined;
    super.discard();
  }
}

const sessionLimit = 64;
export function createGoalRequestSessions(limit = sessionLimit) {
  const sessions = new Map<string, { readonly retained: boolean }>();
  function makeRoom() {
    if (sessions.size < limit) return true;
    const removable = [...sessions].find(([, session]) => !session.retained);
    if (!removable) return false;
    sessions.delete(removable[0]);
    return true;
  }
  function get<S extends { readonly retained: boolean }>(key: string, create: (admit: () => void) => S): S {
    const existing = sessions.get(key);
    if (existing) { sessions.delete(key); sessions.set(key, existing); return existing as S; }
    const session = create(() => {
      if (sessions.get(key) === session) return;
      if (sessions.get(key)?.retained || !makeRoom()) throw new Error('Resolve or discard an existing goal change before another edit.');
      sessions.set(key, session);
    });
    // Idle handles can be reclaimed. A mounted handle must regain admission on run.
    // At capacity an unregistered handle still renders normal Retry/Discard controls.
    if (makeRoom()) sessions.set(key, session);
    return session;
  }
  return {
    checklist: (goalId: string, blockId: string, itemId: string) => get(JSON.stringify([goalId, 'checklist', blockId, itemId]), admit => new RequestSession<ChecklistCommand>(admit)),
    checklistDeadline: (goalId: string, blockId: string, itemId: string) => get(JSON.stringify([goalId, 'checklist-deadline', blockId, itemId]), admit => new ChecklistDeadlineSession(admit)),
    manual: (goalId: string, measureId: string) => get(JSON.stringify([goalId, 'manual', measureId]), admit => new RequestSession<ManualWriteCommand>(admit)),
    binary: (goalId: string) => get(JSON.stringify([goalId, 'binary']), admit => new RequestSession<{ completed: boolean }>(admit)),
  };
}
