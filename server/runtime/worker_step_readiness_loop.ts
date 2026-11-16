import { ensure_workspace_activity } from '../ticket_workspaces/data.ts';
import { manual_start_exempt } from '../conversation/start_admission/index.ts';
/** Explicit per-Ticket start path. Polling and scheduling remain in Unit 9. */
import {
  ConversationStartAborted,
  conversation_operation_scope,
  observe_conversation_operation,
} from './abort.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { automatic_ticket_admitted } from './automatic_work.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import {
  PromptDeliveryStarted,
  PromptDeliveryQueued,
  PromptDeliveryInjected,
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
  type PromptDeliveryFate,
  type ConversationSystem,
} from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import type { WorkerTypeRegistry } from '../worker_types/registry.ts';
import * as tickets_data from '../tickets/data/index.ts';
import * as decision_messages from '../tickets/decision_messages.ts';
import { deliver_ticket_decision_messages } from './decision_message_delivery.ts';
import * as conversation_start from './conversation_start.ts';
import * as worker_memory from './worker_memory.ts';
import * as worker_step_readiness from './worker_step_readiness.ts';
import {
  compose_worker_step_prompt,
  mid_step_memory_loss_prompt,
} from './logic/worker_step_prompt.ts';
export const LOOP_SENDER_LABEL = 'loop';
export type WorkerStepDeliveryFate =
  'started' | 'queued' | 'injected' | 'refused' | 'uncertain' | 'suppressed';
export class WorkerStepStartResult {
  readonly started: boolean;
  readonly delivery_fate: WorkerStepDeliveryFate | null;
  constructor(options: { started: boolean; delivery_fate?: WorkerStepDeliveryFate | null }) {
    this.started = options.started;
    this.delivery_fate = options.delivery_fate ?? null;
    if (this.started !== ['started', 'queued', 'injected'].includes(this.delivery_fate ?? '')) {
      throw new Error('started must match the delivery fate');
    }
  }
  static from_delivery_fate(fate: PromptDeliveryFate): WorkerStepStartResult {
    if (fate instanceof PromptDeliveryStarted)
      return new WorkerStepStartResult({ started: true, delivery_fate: 'started' });
    if (fate instanceof PromptDeliveryQueued)
      return new WorkerStepStartResult({ started: true, delivery_fate: 'queued' });
    if (fate instanceof PromptDeliveryInjected)
      return new WorkerStepStartResult({ started: true, delivery_fate: 'injected' });
    if (fate instanceof PromptDeliveryRefused)
      return new WorkerStepStartResult({ started: false, delivery_fate: 'refused' });
    if (fate instanceof PromptDeliverySuppressed)
      return new WorkerStepStartResult({ started: false, delivery_fate: 'suppressed' });
    if (fate instanceof PromptDeliveryUncertain)
      return new WorkerStepStartResult({ started: false, delivery_fate: 'uncertain' });
    throw new Error('unknown delivery fate');
  }
}
export interface WorkerStepStartOptions {
  automatic_tickets_created_after?: number;
  signal?: AbortSignal;
  connect_database: () => Database;
  conversation_system: ConversationSystem;
  worker_type_registry: WorkerTypeRegistry;
  now: () => number;
}
export async function start_ready_worker_step(
  ticket_id: string,
  options: WorkerStepStartOptions,
): Promise<WorkerStepStartResult> {
  return options.conversation_system.workspace_admission.operation(async (admitted) => {
    if (!admitted) return new WorkerStepStartResult({ started: false });
    if (
      !options
        .connect_database()
        .read((sql) =>
          automatic_ticket_admitted(sql, ticket_id, options.automatic_tickets_created_after),
        )
    )
      return new WorkerStepStartResult({ started: false });
    const decision_fate = await deliver_ticket_decision_messages(ticket_id, options);
    if (decision_fate !== null) return WorkerStepStartResult.from_delivery_fate(decision_fate);
    return _admitted_start_ready_worker_step(ticket_id, options);
  });
}
async function _admitted_start_ready_worker_step(
  ticket_id: string,
  options: WorkerStepStartOptions,
): Promise<WorkerStepStartResult> {
  // The connection capability is borrowed. Its application owner closes it.
  const database = options.connect_database();
  const system = options.conversation_system;
  const starts = manual_start_exempt() ? undefined : system.start_admission;
  let automatic_identity: string | null = null;
  let automatic_sent = false;
  try {
    return await conversation_start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
      const ticket = database.read((sql) => tickets_data.read_ticket(sql, ticket_id));
      let conversation_id = ticket.conversation_id;
      if (
        conversation_id !== null &&
        (await observe_conversation_operation(
          () => system.is_running(conversation_id!),
          options.signal,
        ))
      ) {
        starts?.finish_unbound(`worker-preparation:${ticket.id}`);
        return new WorkerStepStartResult({ started: false });
      }
      if (
        database.read((sql) =>
          conversation_start.configured_ticket_execution_refusal(database, sql, ticket),
        ) !== null
      ) {
        starts?.finish_unbound(`worker-preparation:${ticket.id}`);
        return new WorkerStepStartResult({ started: false });
      }
      if (
        !database.read((sql) =>
          worker_step_readiness.is_ready_for_worker_step(sql, ticket, {
            worker_type_definition: ticket.workflow,
          }),
        )
      )
        return new WorkerStepStartResult({ started: false });
      if (starts) {
        automatic_identity = `worker-preparation:${ticket.id}`;
        if (
          !starts.try_start({
            identity: automatic_identity,
            work_class: 'worker',
            destination: ticket.machine_id,
          })
        ) {
          automatic_identity = null;
          return new WorkerStepStartResult({ started: false });
        }
      }
      const claimed = database.write((sql) =>
        tickets_data.claim_ticket_for_worker_step(sql, ticket_id, {
          readiness_check: worker_step_readiness.is_ready_for_worker_step,
          now: options.now(),
        }),
      );
      if (claimed === null) return new WorkerStepStartResult({ started: false });
      const taken_claim = claimed.worker_step_claim;
      const taken_claim_revision = claimed.worker_step_claim_revision;
      const paired_opener = database.read(
        (sql) =>
          sql.get('SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=? AND stage=?', [
            ticket_id,
            claimed.stage,
          ]) !== undefined,
      );
      const sender_message_id = worker_memory.worker_step_message_id_for_claim(
        ticket.id,
        taken_claim_revision,
      );
      let dispatch_admitted = true;
      if (starts && automatic_identity !== null) {
        dispatch_admitted = starts.transfer(automatic_identity, {
          identity: sender_message_id,
          work_class: 'worker',
          destination: claimed.machine_id,
        });
        automatic_identity = sender_message_id;
      }
      function give_the_claim_back(opener_succeeded = false): void {
        database.write((sql) => {
          const released = tickets_data.release_worker_step_claim(sql, ticket_id, {
            expected_claim: taken_claim,
            expected_claim_revision: taken_claim_revision,
            now: options.now(),
          });
          if (released && paired_opener && !opener_succeeded) {
            tickets_data.forget_user_stage_opener(sql, ticket_id, { stage: claimed!.stage });
          }
        });
      }
      if (!dispatch_admitted) {
        give_the_claim_back();
        return new WorkerStepStartResult({ started: false });
      }
      let fate: PromptDeliveryFate;
      try {
        const prompt = database.read((sql) => {
          const current_ticket = tickets_data.read_ticket(sql, ticket_id);
          return compose_worker_step_prompt(current_ticket, {
            worker_type_definition: claimed.workflow,
          });
        });
        database.write((sql) => {
          tickets_data.mark_worker_step_dispatch_started(sql, ticket_id, taken_claim_revision);
          ensure_workspace_activity(sql, ticket_id, options.now());
        });
        const delivered = await conversation_start.send_to_ticket_conversation(
          system,
          database,
          ticket_id,
          text_message_content(prompt.model_text),
          {
            conversation_id: claimed.conversation_id,
            created_conversation_id: conversation_start.new_conversation_id(),
            sender_label: LOOP_SENDER_LABEL,
            mode: 'queue',
            sender_message_id,
            worker_type_registry: options.worker_type_registry,
            retain_definite_start_refusal: !paired_opener,
            workspace_activity_recorded: true,
            now: options.now(),
            signal: options.signal,
          },
        );
        conversation_id = delivered.conversation_id;
        fate = delivered.fate;
        automatic_sent = true;
      } catch (error) {
        if (error instanceof ConversationStartAborted) throw error;
        console.error(
          `worker step could not be started (ticket=${ticket_id} conversation=${conversation_id})`,
          error,
        );
        give_the_claim_back();
        return new WorkerStepStartResult({ started: false });
      }
      if (starts && automatic_identity !== null) {
        if (fate instanceof PromptDeliveryRefused || fate instanceof PromptDeliverySuppressed)
          starts.finish(automatic_identity);
        else if (fate instanceof PromptDeliveryUncertain) starts.uncertain(automatic_identity);
      }
      if (fate instanceof PromptDeliveryRefused) {
        console.error(
          `worker step send was refused (ticket=${ticket_id} conversation=${conversation_id} reason=${fate.refusal_reason})`,
        );
        if (
          !paired_opener &&
          (fate.refusal_reason === 'backend_did_not_start' ||
            fate.refusal_reason === 'model_resolution_failed')
        ) {
          database.write((sql) =>
            tickets_data.mark_worker_step_claim_errored(sql, ticket_id, {
              expected_claim: taken_claim,
              expected_claim_revision: taken_claim_revision,
              now: options.now(),
              error_reason: `could not start (${fate.refusal_reason})`,
            }),
          );
        } else give_the_claim_back();
        return WorkerStepStartResult.from_delivery_fate(fate);
      }
      if (fate instanceof PromptDeliverySuppressed) {
        give_the_claim_back();
        return WorkerStepStartResult.from_delivery_fate(fate);
      }
      if (fate instanceof PromptDeliveryUncertain) {
        const marked = database.write((sql) =>
          tickets_data.mark_worker_step_claim_errored(sql, ticket_id, {
            expected_claim: taken_claim,
            expected_claim_revision: taken_claim_revision,
            now: options.now(),
            error_reason: 'delivery uncertain',
          }),
        );
        if (marked)
          console.error(
            `worker step delivery was uncertain (ticket=${ticket_id} conversation=${conversation_id})`,
          );
        return WorkerStepStartResult.from_delivery_fate(fate);
      }
      if (paired_opener) give_the_claim_back(true);
      return WorkerStepStartResult.from_delivery_fate(fate);
    }, options.signal);
  } finally {
    if (starts && automatic_identity !== null && !automatic_sent)
      starts.finish_unbound(automatic_identity);
  }
}

export async function tell_a_worker_it_lost_its_memory(
  ticket_id: string,
  options: WorkerStepStartOptions,
): Promise<boolean> {
  return options.conversation_system.workspace_admission.operation(async (admitted) => {
    if (!admitted) return false;
    const database = options.connect_database();
    if (
      !database.read((sql) =>
        automatic_ticket_admitted(sql, ticket_id, options.automatic_tickets_created_after),
      )
    )
      return false;
    return conversation_start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
      const ticket = database.read((sql) => tickets_data.read_ticket(sql, ticket_id));
      const conversation_id = ticket.conversation_id;
      if (
        conversation_id === null ||
        !database.read((sql) => worker_memory.ticket_needs_memory_notice(sql, ticket_id))
      )
        return false;
      if (
        worker_memory.an_answer_is_already_waiting(
          await observe_conversation_operation(
            () => options.conversation_system.sender_message_ids_awaiting_outcome(conversation_id),
            options.signal,
          ),
        )
      )
        return false;
      try {
        if (!database.read((sql) => worker_memory.ticket_needs_memory_notice(sql, ticket_id)))
          return false;
        const delivered = await conversation_start.send_to_ticket_conversation(
          options.conversation_system,
          database,
          ticket_id,
          text_message_content(mid_step_memory_loss_prompt(ticket)),
          {
            conversation_id,
            sender_label: LOOP_SENDER_LABEL,
            mode: 'queue',
            sender_message_id: worker_memory.new_memory_notice_message_id(),
            worker_type_registry: options.worker_type_registry,
            now: options.now(),
            signal: options.signal,
          },
        );
        if (
          delivered.fate instanceof PromptDeliveryRefused ||
          delivered.fate instanceof PromptDeliverySuppressed
        )
          return false;
        return true;
      } catch (error) {
        if (error instanceof ConversationStartAborted) throw error;
        console.error(
          `memory-loss notice could not be sent (ticket=${ticket_id} conversation=${conversation_id})`,
          error,
        );
        return false;
      }
    }, options.signal);
  });
}

/** Candidate scanning, timer backstop, and per-Ticket task ownership. */
export class WorkerStepReadinessLoop {
  private readonly controllers = new Map<string, AbortController>();
  private readonly in_flight = new Map<string, Promise<unknown>>();
  private readonly in_flight_counts = new Map<string, number>();
  private readonly ticket_destinations = new Map<string, string>();
  private last_destination: string | null = null;
  private readonly actual = new Set<Promise<void>>();
  private closing: Promise<void> | undefined;
  private shutdown_settlement: Promise<void> | undefined;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private interval_ms: number | undefined;
  private stop_requested = false;
  private poll_queued = false;
  private readonly options: WorkerStepStartOptions & { before_poll?: () => boolean };
  constructor(options: WorkerStepStartOptions & { before_poll?: () => boolean }) {
    this.options = options;
  }
  private ready_ticket_ids(): string[] {
    const database = this.options.connect_database();
    return database.read((sql) => {
      const rows = sql.all<{ id: string }>(`SELECT t.id FROM tickets t
        WHERE t.integrity_error_reason IS NULL AND t.is_backlogged=0
          AND t.worker_step_claim='none' AND t.pending_proposal IS NULL
          AND NOT EXISTS (SELECT 1 FROM json_each(t.workflow_json, '$.stages') stage
            WHERE json_extract(stage.value, '$.id')=t.stage
              AND json_extract(stage.value, '$.is_terminal'))
        ORDER BY t.updated_at, t.id`);
      this.ticket_destinations.clear();
      let ready = rows
        .filter(({ id }) => {
          try {
            const ticket = tickets_data.read_ticket(sql, id);
            if (
              conversation_start.configured_ticket_execution_refusal(database, sql, ticket) !== null
            )
              return false;
            if (
              !worker_step_readiness.is_ready_for_worker_step(sql, ticket, {
                worker_type_definition: ticket.workflow,
              })
            )
              return false;
            this.ticket_destinations.set(id, ticket.machine_id);
            return true;
          } catch (error) {
            if (error instanceof PlannerError && error.code === 'not_found') return false;
            throw error;
          }
        })
        .map(({ id }) => id);
      const starts = this.options.conversation_system.start_admission;
      if (starts) {
        starts.retain_deferred(
          'worker-preparation:',
          new Set(ready.map((id) => `worker-preparation:${id}`)),
        );
        ready.sort(
          (left, right) =>
            starts.priority([`worker-preparation:${left}`])[1] -
            starts.priority([`worker-preparation:${right}`])[1],
        );
      }
      const groups = new Map<string, string[]>();
      for (const id of ready) {
        const destination = this.ticket_destinations.get(id)!;
        const group = groups.get(destination) ?? [];
        group.push(id);
        groups.set(destination, group);
      }
      let destinations = [...groups.keys()];
      const previous =
        this.last_destination === null ? -1 : destinations.indexOf(this.last_destination);
      if (previous >= 0)
        destinations = [
          ...destinations.slice(previous + 1),
          ...destinations.slice(0, previous + 1),
        ];
      ready = [];
      while (destinations.length) {
        const destination = destinations.shift()!;
        const group = groups.get(destination)!;
        ready.push(group.shift()!);
        if (group.length) destinations.push(destination);
      }
      for (const id of decision_messages.pending_ticket_ids(sql)) {
        if (ready.includes(id)) continue;
        ready.push(id);
        this.ticket_destinations.set(id, tickets_data.read_ticket(sql, id).machine_id);
      }
      return ready;
    });
  }
  private schedule(ticket_id: string, notice = false): boolean {
    if (this.stop_requested || this.in_flight.has(ticket_id)) return false;
    const destination =
      this.ticket_destinations.get(ticket_id) ??
      this.options
        .connect_database()
        .read(
          (sql) =>
            sql.get<{ machine_id: string }>('SELECT machine_id FROM tickets WHERE id=?', [
              ticket_id,
            ])?.machine_id,
        );
    if (destination === undefined) return false;
    const starts = this.options.conversation_system.start_admission;
    if (starts && (this.in_flight_counts.get(destination) ?? 0) >= starts.limit) return false;
    if (
      !this.options
        .connect_database()
        .read((sql) =>
          automatic_ticket_admitted(sql, ticket_id, this.options.automatic_tickets_created_after),
        )
    )
      return false;
    const controller = new AbortController();
    const operations = conversation_operation_scope(controller.signal);
    this.controllers.set(ticket_id, controller);
    const options = { ...this.options, signal: controller.signal };
    const task = Promise.resolve().then<unknown>(() =>
      notice
        ? tell_a_worker_it_lost_its_memory(ticket_id, options)
        : start_ready_worker_step(ticket_id, options),
    );
    this.in_flight.set(ticket_id, task);
    this.in_flight_counts.set(destination, (this.in_flight_counts.get(destination) ?? 0) + 1);
    this.last_destination = destination;
    const actual = task
      .then(
        () => {},
        () => {},
      )
      .then(() => operations.settled());
    this.actual.add(actual);
    void actual.finally(() => this.actual.delete(actual)).catch(() => {});
    void task
      .catch((error) => {
        if (error instanceof ConversationStartAborted) return;
        console.error(
          `worker step task ended in an unhandled failure (ticket=${ticket_id})`,
          error,
        );
      })
      .finally(() => {
        this.in_flight.delete(ticket_id);
        this.controllers.delete(ticket_id);
        const count = this.in_flight_counts.get(destination)! - 1;
        if (count) this.in_flight_counts.set(destination, count);
        else this.in_flight_counts.delete(destination);
      });
    return true;
  }
  poll_once(): string[] {
    if (this.stop_requested || this.options.before_poll?.()) return [];
    const ids = this.ready_ticket_ids().filter((id) => this.schedule(id));
    try {
      this.tell_every_worker_that_lost_its_memory();
    } catch (error) {
      console.error('memory-loss notices could not be scanned for', error);
    }
    return ids;
  }
  tell_every_worker_that_lost_its_memory(): string[] {
    return this.options
      .connect_database()
      .read(worker_memory.ticket_ids_holding_an_unanswered_memory_loss)
      .filter((id) => this.schedule(id, true));
  }
  async drain(): Promise<void> {
    const results = await Promise.allSettled([...this.in_flight.values()]);
    for (const result of results) {
      if (result.status === 'rejected' && !(result.reason instanceof ConversationStartAborted))
        throw result.reason;
    }
  }
  wake(): void {
    if (this.stop_requested || this.interval_ms === undefined || this.poll_queued) return;
    this.poll_queued = true;
    queueMicrotask(() => {
      this.poll_queued = false;
      if (!this.stop_requested) this.tick();
    });
  }
  private tick(): void {
    clearTimeout(this.timer);
    try {
      this.poll_once();
    } catch (error) {
      console.error('worker-step readiness poll failed', error);
    }
    if (!this.stop_requested) {
      const fallback = this.interval_ms! / 1000;
      const delay =
        this.options.conversation_system.start_admission?.poll_delay(fallback) ?? fallback;
      this.timer = setTimeout(() => this.tick(), delay * 1000);
    }
  }
  start(interval_seconds: number): void {
    if (this.interval_ms !== undefined || this.stop_requested)
      throw new Error('worker-step readiness loop already started or stopped');
    this.interval_ms = interval_seconds * 1000;
    this.tick();
  }
  settled(): Promise<void> {
    return this.shutdown_settlement ?? Promise.all([this.drain(), ...this.actual]).then(() => {});
  }
  stop(options: StopOptions): Promise<void> {
    if (this.closing) return this.closing;
    this.stop_requested = true;
    clearTimeout(this.timer);
    const deadline = options.deadline;
    this.shutdown_settlement = Promise.allSettled([this.drain(), ...this.actual]).then(
      (results) => {
        const failure = results.find((result) => result.status === 'rejected');
        if (failure?.status === 'rejected') throw failure.reason;
      },
    );
    this.closing = observe_shutdown(
      this.shutdown_settlement,
      { deadline },
      'Shutdown deadline expired; ownership retained: worker readiness',
      ['worker readiness'],
    );
    // Cancel caller observers now. Their raw operation scopes remain owned above.
    for (const controller of this.controllers.values()) controller.abort();
    return this.closing;
  }
}
