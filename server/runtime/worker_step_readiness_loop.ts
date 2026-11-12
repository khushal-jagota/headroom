/** Explicit per-Ticket start path. Polling and scheduling remain in Unit 9. */
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
import * as revision_feedback from '../tickets/revision_feedback.ts';
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
  return conversation_start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    const ticket = database.read((sql) => tickets_data.read_ticket(sql, ticket_id));
    let conversation_id = ticket.conversation_id;
    if (conversation_id !== null && (await system.is_running(conversation_id))) {
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
    const sender_message_id = worker_memory.new_worker_step_message_id();
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
    let pending_revision_feedback: revision_feedback.PendingRevisionFeedback | null = null;
    let fate: PromptDeliveryFate;
    try {
      const prompt = database.read((sql) => {
        const current_ticket = tickets_data.read_ticket(sql, ticket_id);
        pending_revision_feedback = revision_feedback.snapshot(sql, ticket_id);
        return compose_worker_step_prompt(current_ticket, {
          worker_type_definition: claimed.workflow,
          revision_feedback: pending_revision_feedback,
          memory_was_lost: worker_memory.conversation_holds_an_unanswered_memory_loss(
            sql,
            claimed.conversation_id,
          ),
        });
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
          retain_backend_start_failure: !paired_opener,
          now: options.now(),
        },
      );
      conversation_id = delivered.conversation_id;
      fate = delivered.fate;
    } catch (error) {
      console.error(
        `worker step could not be started (ticket=${ticket_id} conversation=${conversation_id})`,
        error,
      );
      give_the_claim_back();
      return new WorkerStepStartResult({ started: false });
    }
    if (fate instanceof PromptDeliveryRefused) {
      console.error(
        `worker step send was refused (ticket=${ticket_id} conversation=${conversation_id} reason=${fate.refusal_reason})`,
      );
      if (!paired_opener && fate.refusal_reason === 'backend_did_not_start') {
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
    // TypeScript does not track assignment from the synchronous read callback.
    const feedback = pending_revision_feedback as revision_feedback.PendingRevisionFeedback | null;
    if (feedback !== null) {
      try {
        database.write((sql) => revision_feedback.acknowledge(sql, ticket_id, feedback.revision));
      } catch (error) {
        console.error(
          `delivered revision feedback could not be acknowledged (ticket=${ticket_id})`,
          error,
        );
      }
    }
    return WorkerStepStartResult.from_delivery_fate(fate);
  });
}

export async function tell_a_worker_it_lost_its_memory(
  ticket_id: string,
  options: WorkerStepStartOptions,
): Promise<boolean> {
  return options.conversation_system.workspace_admission.operation(async (admitted) => {
    if (!admitted) return false;
    const database = options.connect_database();
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
          await options.conversation_system.held_prompts(conversation_id),
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
          },
        );
        if (
          delivered.fate instanceof PromptDeliveryRefused ||
          delivered.fate instanceof PromptDeliverySuppressed
        )
          return false;
        return true;
      } catch (error) {
        console.error(
          `memory-loss notice could not be sent (ticket=${ticket_id} conversation=${conversation_id})`,
          error,
        );
        return false;
      }
    });
  });
}

/** Candidate scanning, timer backstop, and per-Ticket task ownership. */
export class WorkerStepReadinessLoop {
  private readonly in_flight = new Map<string, Promise<unknown>>();
  private timer: ReturnType<typeof setTimeout> | undefined;
  private interval_ms: number | undefined;
  private stop_requested = false;
  private poll_queued = false;
  private readonly options: WorkerStepStartOptions & { before_poll?: () => boolean };
  constructor(options: WorkerStepStartOptions & { before_poll?: () => boolean }) {
    this.options = options;
  }
  private ready_ticket_ids(): string[] {
    return this.options.connect_database().read((sql) => {
      const rows = sql.all<{ id: string }>(`SELECT t.id FROM tickets t
        WHERE t.integrity_error_reason IS NULL AND t.is_backlogged=0
          AND t.worker_step_claim='none' AND t.pending_proposal IS NULL
          AND NOT EXISTS (SELECT 1 FROM json_each(t.workflow_json, '$.stages') stage
            WHERE json_extract(stage.value, '$.id')=t.stage
              AND json_extract(stage.value, '$.is_terminal'))
        ORDER BY t.updated_at, t.id`);
      return rows
        .filter(({ id }) => {
          try {
            const ticket = tickets_data.read_ticket(sql, id);
            return worker_step_readiness.is_ready_for_worker_step(sql, ticket, {
              worker_type_definition: ticket.workflow,
            });
          } catch (error) {
            if (error instanceof PlannerError && error.code === 'not_found') return false;
            throw error;
          }
        })
        .map(({ id }) => id);
    });
  }
  private schedule(ticket_id: string, notice = false): boolean {
    if (this.stop_requested || this.in_flight.has(ticket_id)) return false;
    const task = Promise.resolve().then<unknown>(() =>
      notice
        ? tell_a_worker_it_lost_its_memory(ticket_id, this.options)
        : start_ready_worker_step(ticket_id, this.options),
    );
    this.in_flight.set(ticket_id, task);
    void task
      .catch((error) =>
        console.error(
          `worker step task ended in an unhandled failure (ticket=${ticket_id})`,
          error,
        ),
      )
      .finally(() => this.in_flight.delete(ticket_id));
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
      if (result.status === 'rejected') throw result.reason;
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
    if (!this.stop_requested) this.timer = setTimeout(() => this.tick(), this.interval_ms);
  }
  start(interval_seconds: number): void {
    if (this.interval_ms !== undefined || this.stop_requested)
      throw new Error('worker-step readiness loop already started or stopped');
    this.interval_ms = interval_seconds * 1000;
    this.tick();
  }
  async stop(): Promise<void> {
    this.stop_requested = true;
    clearTimeout(this.timer);
    await this.drain();
  }
}
