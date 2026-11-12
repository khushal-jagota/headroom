/** FIFO process ownership. No lock survives a provider call unless the rule requires it. */
import type { BackendChild, BackendEventSink, TurnToken } from '../backends/contracts.ts';
import type { ConversationRecord, RunningTurn } from '../record/contracts.ts';
import type { ConversationPhase, Message, ReservedTurn, SenderAdmission } from './contracts.ts';

export class Deferred<T> {
  readonly promise: Promise<T>;
  #resolve!: (value: T) => void;
  settled = false;
  constructor() {
    this.promise = new Promise<T>((resolve) => {
      this.#resolve = resolve;
    });
  }
  resolve(value: T): void {
    if (this.settled) return;
    this.settled = true;
    this.#resolve(value);
  }
}
export class FifoLock {
  #locked = false;
  readonly #waiters: (() => void)[] = [];
  get locked(): boolean {
    return this.#locked;
  }
  async acquire(): Promise<() => void> {
    if (this.#locked) {
      await new Promise<void>((resolve) => this.#waiters.push(resolve));
    } else {
      this.#locked = true;
    }
    let released = false;
    return () => {
      if (released) throw new Error('conversation lock released twice');
      released = true;
      const next = this.#waiters.shift();
      if (next) next();
      else this.#locked = false;
    };
  }
  async run<T>(work: () => T | Promise<T>): Promise<T> {
    const release = await this.acquire();
    try {
      return await work();
    } finally {
      release();
    }
  }
}
export class ConversationState {
  record: ConversationRecord;
  readonly lock = new FifoLock();
  phase: ConversationPhase = 'idle';
  phase_settled = new Deferred<void>();
  readonly held_prompts: Message[] = [];
  readonly admitted_sender_messages = new Map<string, SenderAdmission>();
  readonly sender_messages_being_delivered = new Map<string, SenderAdmission>();
  readonly settlement_tasks = new Set<Promise<unknown>>();
  readonly backend_events: (() => Promise<void>)[] = [];
  backend_event_pump: Promise<void> | null = null;
  reserved_turn: ReservedTurn | null = null;
  running_turn: RunningTurn | null = null;
  last_ended_turn_token: TurnToken | null = null;
  last_ended_turn_was_automatic_compaction = false;
  child: BackendChild | null = null;
  child_event_sink: BackendEventSink | null = null;
  child_is_quarantined = false;
  delivery_uncertain = false;
  has_delivered_prompt = false;
  being_killed = false;
  last_touched_monotonic = 0;
  model_thinking_shown_at_monotonic = 0;
  constructor(record: ConversationRecord) {
    this.record = record;
    this.phase_settled.resolve();
  }
}
export function same_turn(left: TurnToken | null, right: TurnToken | null): boolean {
  return (
    left !== null &&
    right !== null &&
    left.conversation_id === right.conversation_id &&
    left.turn_number === right.turn_number
  );
}
export function set_phase(state: ConversationState, phase: ConversationPhase): void {
  state.phase = phase;
  if (phase === 'idle' || phase === 'running') {
    state.phase_settled.resolve();
  } else if (state.phase_settled.settled) {
    state.phase_settled = new Deferred<void>();
  }
}
export function own_settlement<T>(state: ConversationState, task: Promise<T>): Promise<T> {
  state.settlement_tasks.add(task);
  void task.finally(() => state.settlement_tasks.delete(task)).catch(() => {});
  return task;
}
