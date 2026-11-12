/** Retain admitted work and deliver each committed outcome through the actual core. */
import { PlannerError } from '../../core/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import {
  PromptDeliveryStarted,
  PromptDeliveryInjected,
  PromptDeliveryQueued,
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
  PromptDeliveryRefusalReason,
  PromptDeliveryMode,
} from '../../conversation/contracts.ts';
import { text_message_content } from '../../conversation/message_content.ts';
import { manager_lifecycle_lock } from '../../managers/service.ts';
import { sender_conversation_link_key } from '../../message_delivery/service.ts';
import { conversation_link_lock } from '../../runtime/conversation_start.ts';
import * as actions from './actions.ts';
import * as data from './data.ts';
import type { CredentialRequestRowBody, RequestDeliveryStatus } from './contracts.ts';
import type { CredentialRequestRuntime } from './runtime.ts';

type Delivery = readonly [RequestDeliveryStatus, string | null];
export class CredentialRequestOperations {
  readonly runtime: CredentialRequestRuntime;
  private readonly tasks = new Set<Promise<unknown>>();

  constructor(runtime: CredentialRequestRuntime) {
    this.runtime = runtime;
  }

  async shield_operation<T>(operation: () => Promise<T>): Promise<T> {
    const task = Promise.resolve().then(() =>
      this.runtime.core.workspace_admission.operation(async (admitted) => {
        if (!admitted) throw new Error('Workspace admission is closed for an owner update.');
        return await operation();
      }),
    );
    this.tasks.add(task);
    void task.then(
      () => this.tasks.delete(task),
      () => this.tasks.delete(task),
    );
    return await task;
  }

  async finish_operations(): Promise<void> {
    while (this.tasks.size !== 0) await Promise.allSettled([...this.tasks]);
  }

  async dispatch(request_id: string, attempt_id: string | null): Promise<CredentialRequestRowBody> {
    const { database, core } = this.runtime;
    if (attempt_id === null) return database.read((sql) => data.read(sql, request_id));
    const { row, attempt } = database.read((sql) => ({
      row: data.row_for(sql, request_id),
      attempt: sql.get<data.CredentialRequestDeliveryAttemptRow>(
        'SELECT * FROM credential_request_delivery_attempts WHERE attempt_id=?',
        [attempt_id],
      )!,
    }));
    const origin = row.conversation_id;
    const principal: Principal = { kind: row.requester_kind, id: row.requester_id };
    const deliver = async (): Promise<Delivery> => {
      let link_key: string;
      try {
        link_key = sender_conversation_link_key(database, principal);
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        return ['refused', 'requesting_conversation_retired'];
      }
      return await conversation_link_lock(link_key).run(async (): Promise<Delivery> => {
        try {
          database.read((sql) => actions.origin_conversation(sql, {
            principal,
            conversationId: origin,
          }));
        } catch (error) {
          if (!(error instanceof PlannerError)) throw error;
          return ['refused', 'requesting_conversation_retired'];
        }
        let fate;
        try {
          fate = await core.send(origin, text_message_content(attempt.prompt), {
            sender_label: 'Panels credential request result',
            sender_message_id: attempt.sender_message_id,
            mode: PromptDeliveryMode.steer,
            record_refusal: true,
          });
        } catch {
          // A missing response cannot establish that the provider received nothing.
          return ['uncertain', null];
        }
        if (fate instanceof PromptDeliveryStarted) return ['started', null];
        if (fate instanceof PromptDeliveryInjected) return ['injected', null];
        if (fate instanceof PromptDeliveryQueued) return ['queued', null];
        if (fate instanceof PromptDeliveryRefused) return [
          fate.refusal_reason === PromptDeliveryRefusalReason.write_to_backend_failed
            ? 'uncertain' : 'refused',
          fate.refusal_reason,
        ];
        if (fate instanceof PromptDeliverySuppressed) return ['canceled', null];
        if (fate instanceof PromptDeliveryUncertain) return ['uncertain', null];
        throw new Error('unknown credential result delivery fate');
      });
    };
    // Match Manager deletion and ordinary addressed delivery: lifecycle before association.
    const [status, reason] = principal.kind === 'manager'
      ? await manager_lifecycle_lock(principal.id).run(deliver)
      : await deliver();
    return await core.record_events_transaction(origin, (tx) => {
      const event = data.settle_attempt(tx, attempt_id, status, reason, this.runtime.now());
      return { value: data.read(tx, request_id), payloads: event === null ? [] : [event] };
    });
  }
}
