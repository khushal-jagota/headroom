/** Durable notices use the same conversation doors as ordinary messages. */
import { randomUUID } from 'node:crypto';
import type {
  Database,
  ReadSql,
  TransactionSql,
  WriteOptions,
} from '../core/database/contracts.ts';
import type { Clock } from '../core/clock.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import {
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
} from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import * as conversation_start from '../runtime/conversation_start.ts';
import { read_manager } from '../managers/data.ts';
import { manager_lifecycle_lock } from '../managers/service.ts';
import { read_ticket } from '../tickets/data/index.ts';
import { CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import * as data from './data.ts';
import type { WakeBatch } from './contracts.ts';

export const WAKE_SENDER_LABEL = 'Panels';
export interface WakeWorkspaceContext {
  db_path: string;
  workspace_root: string | null;
}
export interface WakeRuntimeContext {
  workspace_context: WakeWorkspaceContext;
  database: Database;
  clock: Clock;
  conversation_system?: ConversationSystem;
  /** The lifecycle owner must fence this callback before it releases the database. */
  assert_database_owned: () => void;
}
export interface ManagerWakeLoopOptions extends WakeRuntimeContext {
  conversation_system: ConversationSystem;
}
export function reconcile_batch_outcomes(database: Database, options: { now: number }): number {
  let settled = 0;
  for (const batch of database.read(data.batches_waiting_for_outcome)) {
    if (batch.conversation_id === null) continue;
    const outcome = database.read((sql) =>
      data.delivery_outcome_kind(sql, batch.conversation_id!, batch.sender_message_id),
    );
    if (outcome === 'prompt') {
      database.write((sql) => data.close_delivered_batch(sql, batch.id, options));
      settled += 1;
    } else if (outcome === 'prompt_delivery_refused' || outcome === 'prompt_discarded') {
      database.write((sql) =>
        data.record_batch_failure(sql, batch.id, {
          status: outcome === 'prompt_delivery_refused' ? 'refused' : 'discarded',
          conversation_id: batch.conversation_id,
          now: options.now,
        }),
      );
      settled += 1;
    } else if (outcome === 'prompt_delivery_uncertain' && batch.status !== 'uncertain') {
      database.write((sql) =>
        data.record_batch_uncertain(sql, batch.id, {
          conversation_id: batch.conversation_id,
          process_token: batch.process_token ?? 'unknown',
          now: options.now,
        }),
      );
      settled += 1;
    }
  }
  return settled;
}
/** The observer leaves the core-owned exchange alive when shutdown stops its wait. */
export class WakeObserverStopped extends Error {
  constructor() {
    super('manager wake observer stopped');
    this.name = 'WakeObserverStopped';
  }
}
class WakeDeliveryObserver {
  private stopped = false;
  private readonly waiters = new Set<(error: WakeObserverStopped) => void>();
  private offered: (() => void) | undefined;
  check(): void {
    if (this.stopped) throw new WakeObserverStopped();
  }
  on_offered(operation: () => void): void {
    this.check();
    this.offered = operation;
  }
  settled(): void {
    this.offered = undefined;
  }
  cancel(): void {
    if (this.stopped) return;
    this.stopped = true;
    const error = new WakeObserverStopped();
    // Record uncertainty before any observer can finish and release the database owner.
    try {
      this.offered?.();
    } catch (cause) {
      Object.defineProperty(error, 'cause', { value: cause });
      console.error('manager wake cancellation could not record uncertainty', cause);
    } finally {
      this.offered = undefined;
      for (const reject of this.waiters) reject(error);
      this.waiters.clear();
    }
  }
  observe<T>(operation: () => Promise<T>): Promise<T> {
    this.check();
    return new Promise<T>((resolve, reject) => {
      this.waiters.add(reject);
      try {
        operation()
          .then(resolve, reject)
          .finally(() => this.waiters.delete(reject));
      } catch (error) {
        this.waiters.delete(reject);
        reject(error);
      }
    });
  }
  database(database: Database): Database {
    return {
      read: <T>(work: (sql: ReadSql) => T): T => {
        this.check();
        return database.read(work);
      },
      write: <T>(work: (sql: TransactionSql) => T, options?: WriteOptions): T => {
        this.check();
        return database.write(work, options);
      },
      close: () => {
        throw new Error('the wake observer does not own database closure');
      },
    };
  }
  system(system: ConversationSystem): ConversationSystem {
    const observed = new Set<PropertyKey>(['start_conversation', 'send', 'is_running', 'kill']);
    return new Proxy(system, {
      get: (target, key) => {
        const value = Reflect.get(target, key, target);
        if (typeof value !== 'function') return value;
        return observed.has(key)
          ? (...args: unknown[]) =>
              this.observe(() => Promise.resolve(Reflect.apply(value, target, args)))
          : value.bind(target);
      },
    });
  }
}
export interface DeliverBatchOptions {
  workspace_context: WakeWorkspaceContext;
  database: Database;
  conversation_system: ConversationSystem;
  process_token: string;
  now: () => number;
  observer?: WakeDeliveryObserver;
}
export async function deliver_batch(
  batch: WakeBatch,
  options: DeliverBatchOptions,
): Promise<boolean> {
  const observer = options.observer ?? new WakeDeliveryObserver();
  options = {
    ...options,
    observer,
    conversation_system: observer.system(options.conversation_system),
  };
  const admission = options.conversation_system.workspace_admission?.operation();
  try {
    if (admission && !admission.admitted) return false;
    const operation = () => {
      observer.check();
      return admitted_deliver_batch(batch, options);
    };
    return batch.target.kind === 'manager'
      ? await observer.observe(() => manager_lifecycle_lock(batch.target.id).run(operation))
      : await operation();
  } finally {
    observer.settled();
    admission?.release();
  }
}
async function admitted_deliver_batch(
  batch: WakeBatch,
  options: DeliverBatchOptions,
): Promise<boolean> {
  const { database, conversation_system, process_token, now } = options;
  const observer = options.observer!;
  observer.check();
  const helper_database = observer.database(database);
  conversation_start.configure_conversation_start(helper_database, options.workspace_context);
  const target = batch.target;
  let agent_key: string | null;
  let required_manager_id: string | null = null;
  let resolved: ReturnType<typeof conversation_start.agent_resolve>;
  try {
    if (target.kind === 'manager') {
      const item = database.read((sql) => read_manager(sql, target.id).item);
      agent_key = item.manager_agent_key;
      resolved = conversation_start.manager_resolve(database, item);
      required_manager_id = target.id;
    } else if (target.kind === 'chief') {
      agent_key = CHIEF_SETTINGS_KEY;
      resolved = conversation_start.agent_resolve(database);
    } else {
      const ticket = database.read((sql) => read_ticket(sql, target.id));
      agent_key = null;
      resolved = conversation_start.worker_resolve(database, ticket);
    }
  } catch (error) {
    if (error instanceof PlannerError && error.code === 'not_found') {
      database.write((sql) => data.close_undeliverable_batch(sql, batch.id, { now: now() }));
      return false;
    }
    throw error;
  }
  const link_key = agent_key === null ? `ticket:${target.id}` : `agent:${agent_key}`;
  return observer.observe(() =>
    conversation_start.conversation_link_lock(link_key).run(async () => {
      observer.check();
      let current: string | null;
      try {
        current =
          agent_key === null
            ? database.read((sql) => read_ticket(sql, target.id).conversation_id)
            : conversation_start.read_agent_conversation(database, agent_key);
      } catch (error) {
        if (error instanceof PlannerError && error.code === 'not_found') {
          database.write((sql) => data.close_undeliverable_batch(sql, batch.id, { now: now() }));
          return false;
        }
        throw error;
      }
      const running = current !== null ? await conversation_system.is_running(current) : false;
      observer.check();
      if (running) {
        database.write((sql) => data.release_pending_batch(sql, batch.id, { process_token }), {
          changeSignal: 'silent',
        });
        return false;
      }
      const created = conversation_start.new_conversation_id();
      const intended_conversation_id = current ?? created;
      database.write((sql) =>
        data.record_batch_offering(sql, batch.id, {
          conversation_id: intended_conversation_id,
          process_token,
          now: now(),
        }),
      );
      observer.on_offered(() =>
        database.write((sql) =>
          data.record_batch_uncertain(sql, batch.id, {
            conversation_id: intended_conversation_id,
            process_token,
            now: now(),
          }),
        ),
      );
      let delivered: conversation_start.DeliveredMessage;
      try {
        const send_options = {
          conversation_id: current,
          created_conversation_id: created,
          sender_label: WAKE_SENDER_LABEL,
          mode: 'queue' as const,
          sender_message_id: batch.sender_message_id,
        };
        delivered =
          agent_key === null
            ? await conversation_start.send_to_ticket_conversation(
                conversation_system,
                helper_database,
                target.id,
                text_message_content(batch.message),
                { ...send_options, now: now() },
              )
            : await conversation_start.send_to_agent_conversation(
                conversation_system,
                helper_database,
                agent_key,
                text_message_content(batch.message),
                resolved,
                { ...send_options, required_manager_id },
              );
      } catch (error) {
        if (error instanceof WakeObserverStopped) throw error;
        database.write((sql) =>
          data.record_batch_uncertain(sql, batch.id, {
            conversation_id: intended_conversation_id,
            process_token,
            now: now(),
          }),
        );
        throw error;
      }
      observer.check();
      observer.settled();
      if (delivered.fate instanceof PromptDeliveryRefused) {
        database.write((sql) =>
          data.record_batch_failure(sql, batch.id, {
            status: 'refused',
            conversation_id: delivered.conversation_id,
            now: now(),
          }),
        );
        return false;
      }
      if (delivered.fate instanceof PromptDeliverySuppressed) {
        reconcile_batch_outcomes(database, { now: now() });
        return false;
      }
      if (delivered.fate instanceof PromptDeliveryUncertain) {
        database.write((sql) =>
          data.record_batch_uncertain(sql, batch.id, {
            conversation_id: delivered.conversation_id ?? intended_conversation_id,
            process_token,
            now: now(),
          }),
        );
        return false;
      }
      if (delivered.conversation_id === null) throw new Error('accepted wake has no conversation');
      database.write((sql) =>
        data.record_batch_accepted(sql, batch.id, {
          conversation_id: delivered.conversation_id,
          process_token,
          now: now(),
        }),
      );
      reconcile_batch_outcomes(database, { now: now() });
      return (
        database.read((sql) =>
          data.delivery_outcome_kind(sql, delivered.conversation_id!, batch.sender_message_id),
        ) === 'prompt'
      );
    }),
  );
}

/** Construction is inert. Only the lifecycle composer explicitly starts the timer. */
export class ManagerWakeLoop {
  readonly process_token = randomUUID().replaceAll('-', '');
  private readonly options: ManagerWakeLoopOptions;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private started = false;
  private stopped = false;
  private pass: Promise<number[]> | undefined;
  private rerun = false;
  private interval = 0;
  private readonly in_flight = new Map<number, Promise<boolean>>();
  private readonly observers = new Map<number, WakeDeliveryObserver>();

  constructor(options: ManagerWakeLoopOptions) {
    this.options = options;
  }
  poll_once(): Promise<number[]> {
    this.options.assert_database_owned();
    if (this.stopped) return Promise.resolve([]);
    if (this.pass) return this.pass;
    const admission = this.options.conversation_system.workspace_admission?.operation();
    if (admission && !admission.admitted) {
      admission.release();
      return Promise.resolve([]);
    }
    this.pass = Promise.resolve()
      .then(() => this.poll_admitted())
      .finally(() => {
        admission?.release();
        this.pass = undefined;
      });
    return this.pass;
  }
  private async poll_admitted(): Promise<number[]> {
    if (this.stopped) return [];
    const { database, clock, conversation_system } = this.options;
    const options = { process_token: this.process_token, now: clock.nowUnix() };
    reconcile_batch_outcomes(database, options);
    database.write((sql) => data.preserve_interrupted_dispatches(sql, options));
    database.write((sql) => data.recover_accepted_batches_from_other_processes(sql, options));
    const batches = database.read(data.pending_batches);
    const attempted = new Set(batches.map((batch) => `${batch.target.kind}:${batch.target.id}`));
    for (;;) {
      const batch = database.write((sql) => data.claim_next_batch(sql, options), {
        changeSignal: 'silent',
      });
      if (batch === null) break;
      const key = `${batch.target.kind}:${batch.target.id}`;
      if (attempted.has(key)) break;
      attempted.add(key);
      batches.push(batch);
    }
    const ids: number[] = [];
    const deliveries: Promise<unknown>[] = [];
    for (const batch of batches) {
      if (this.stopped || this.in_flight.has(batch.id)) continue;
      ids.push(batch.id);
      const observer = new WakeDeliveryObserver();
      this.observers.set(batch.id, observer);
      const delivery = deliver_batch(batch, {
        observer,
        workspace_context: this.options.workspace_context,
        database,
        conversation_system,
        process_token: this.process_token,
        now: () => clock.nowUnix(),
      });
      this.in_flight.set(batch.id, delivery);
      deliveries.push(
        delivery
          .catch((error: unknown) => {
            if (error instanceof WakeObserverStopped) throw error;
            console.error(
              `manager wake delivery ended in an uncertain failure (batch=${batch.id})`,
              error,
            );
            throw error;
          })
          .finally(() => {
            this.in_flight.delete(batch.id);
            this.observers.delete(batch.id);
          }),
      );
    }
    const results = await Promise.allSettled(deliveries);
    const failed = results.find((result) => result.status === 'rejected');
    if (failed?.status === 'rejected') throw failed.reason;
    return ids;
  }
  start(interval: number): void {
    this.options.assert_database_owned();
    if (this.started) throw new Error('manager wake loop already started');
    if (this.stopped) throw new Error('manager wake loop is stopped');
    this.started = true;
    this.interval = interval * 1000;
    this.wake();
  }
  wake(): void {
    if (!this.started || this.stopped) return;
    if (this.pass) {
      this.rerun = true;
      return;
    }
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = undefined;
      void this.poll_once()
        .catch((error: unknown) => {
          if (error instanceof WakeObserverStopped) return;
          console.error('manager wake poll failed', error);
        })
        .finally(() => {
          if (this.stopped) return;
          if (this.rerun) {
            this.rerun = false;
            this.wake();
          } else {
            this.timer = setTimeout(() => this.wake(), this.interval);
            this.timer.unref();
          }
        });
    }, 0);
    this.timer.unref();
  }
  /** Absolute deadline in monotonic seconds (performance.now()/1000), like Python. */
  async stop(options: { deadline?: number } = {}): Promise<void> {
    this.stopped = true;
    if (this.timer !== undefined) clearTimeout(this.timer);
    const settled = Promise.allSettled([
      ...(this.pass ? [this.pass] : []),
      ...this.in_flight.values(),
    ]);
    const deadline = options.deadline ?? performance.now() / 1000 + 10;
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
      settled,
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, Math.max(0, deadline * 1000 - performance.now()));
      }),
    ]);
    if (timer !== undefined) clearTimeout(timer);
    for (const observer of this.observers.values()) observer.cancel();
    await settled;
  }
}
