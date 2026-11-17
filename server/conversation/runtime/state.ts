/** FIFO process ownership. No lock survives a provider call unless the rule requires it. */
import type {
  BackendChild,
  BackendEventSink,
  TurnToken,
  ChildReference,
} from '../backends/contracts.ts';
import type { BackendSelection } from '../../drivers/core/selection.ts';
import { wait_for_item } from '../../executor/item_budget.ts';
import type { ConversationRecord, RunningTurn } from '../record/contracts.ts';
import type { ConversationPhase, Message, ReservedTurn, SenderAdmission } from './contracts.ts';
import { Deferred } from '../../core/deferred.ts';

export class FifoLock {
  #locked = false;
  readonly #waiters: (() => void)[] = [];
  get locked(): boolean {
    return this.#locked;
  }
  async acquire(deadline?: number): Promise<() => void> {
    if (this.#locked) {
      let resumed = false;
      let resume!: () => void;
      const waiting = new Promise<void>((resolve) => {
        resume = () => {
          resumed = true;
          resolve();
        };
        this.#waiters.push(resume);
      });
      try {
        await wait_for_item(waiting, deadline);
      } catch (error) {
        const position = this.#waiters.indexOf(resume);
        if (position >= 0) this.#waiters.splice(position, 1);
        else if (resumed) this.release_next();
        throw error;
      }
    } else {
      this.#locked = true;
    }
    let released = false;
    return () => {
      if (released) throw new Error('conversation lock released twice');
      released = true;
      this.release_next();
    };
  }
  private release_next(): void {
    const next = this.#waiters.shift();
    if (next) next();
    else this.#locked = false;
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
  closing = false;
  preparing_child: BackendChild | null = null;
  phase: ConversationPhase = 'idle';
  phase_settled = new Deferred<void>();
  readonly held_prompts: Message[] = [];
  readonly admitted_sender_messages = new Map<string, SenderAdmission>();
  readonly sender_messages_being_delivered = new Map<string, SenderAdmission>();
  readonly settlement_tasks = new Set<Promise<unknown>>();
  readonly backend_events: (() => Promise<void>)[] = [];
  backend_event_pump: Promise<void> | null = null;
  backend_event_failures = 0;
  reserved_turn: ReservedTurn | null = null;
  running_turn: RunningTurn | null = null;
  last_ended_turn_token: TurnToken | null = null;
  last_ended_turn_was_automatic_compaction = false;
  child: BackendChild | null = null;
  /** The selection the current child was started or last set with. Only read while `child` is that child. */
  child_selection: { readonly child: BackendChild; readonly selection: BackendSelection } | null =
    null;
  retained_child_reference: ChildReference | null = null;
  retirement_task: Promise<boolean> | null = null;
  retiring_child: BackendChild | null = null;
  child_event_sink: BackendEventSink | null = null;
  readonly steer_settlements = new Set<Deferred<void>>();
  late_steer_generation = 0;
  readonly late_steers = new Set<Promise<void>>();
  readonly background_tasks_stopped_since_the_agent_last_acted = new Map<string, string>();
  child_is_quarantined = false;
  /** The session-load refusal already recorded and logged since a child last started. */
  last_session_load_refusal_detail: string | null = null;
  local_child_retirement_proven = false;
  local_preparation_cancelled = false;
  custody_ready: Promise<void> | null = null;
  delivery_uncertain = false;
  has_delivered_prompt = false;
  being_killed = false;
  restart_retired = false;
  /** In memory only: a save gave up, so this conversation refuses new work until a restart. */
  save_failure: unknown = null;
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

/** Upkeep never renews its own automatic-compaction eligibility. */
export function turn_is_maintenance(running: RunningTurn): boolean {
  return running.automatic_compaction || running.memory_notice_only;
}
