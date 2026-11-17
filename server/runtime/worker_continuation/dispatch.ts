import type { WorkerStepStartOptions } from '../worker_step_readiness_loop.ts';
import { WorkerStepStartResult, LOOP_SENDER_LABEL } from '../worker_step_readiness_loop.ts';
import {
  unsent_worker_continuation,
  admit_worker_continuation,
  mark_continuation_error,
} from './index.ts';
import { CONTINUATION_PROMPT, CONTINUATION_PREFIX } from './logic/identity.ts';
import { manual_start_exempt } from '../../conversation/start_admission/index.ts';
import {
  PromptDeliveryRefused,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
} from '../../conversation/contracts.ts';
import { text_message_content } from '../../conversation/message_content.ts';
import { observe_conversation_operation, ConversationStartAborted } from '../abort.ts';
import * as conversation_start from '../conversation_start.ts';
import * as tickets_data from '../../tickets/data/index.ts';

export async function continue_unfinished_worker_step(
  ticket_id: string,
  options: WorkerStepStartOptions,
): Promise<WorkerStepStartResult | null> {
  const database = options.connect_database();
  const system = options.conversation_system;
  return conversation_start.conversation_link_lock(`ticket:${ticket_id}`).run(async () => {
    const source = database.read((sql) =>
      unsent_worker_continuation(sql, tickets_data.read_ticket(sql, ticket_id)),
    );
    if (source === null) return null;
    if (
      await observe_conversation_operation(
        () => system.is_running(source.conversation_id),
        options.signal,
      )
    )
      return new WorkerStepStartResult({ started: false });
    if (
      Array.from(
        await observe_conversation_operation(
          () => system.sender_message_ids_awaiting_outcome(source.conversation_id),
          options.signal,
        ),
      ).some((identity) => identity.startsWith(CONTINUATION_PREFIX))
    )
      return new WorkerStepStartResult({ started: false });
    const ticket = database.read((sql) => tickets_data.read_ticket(sql, ticket_id));
    if (
      database.read((sql) =>
        conversation_start.configured_ticket_execution_refusal(database, sql, ticket),
      ) !== null
    )
      return new WorkerStepStartResult({ started: false });
    const starts = manual_start_exempt() ? undefined : system.start_admission;
    if (
      starts &&
      !starts.try_start({
        identity: source.sender_message_id,
        work_class: 'worker',
        destination: ticket.machine_id,
      })
    )
      return new WorkerStepStartResult({ started: false });
    let sent = false;
    try {
      if (
        database.write((tx) =>
          admit_worker_continuation(
            tx,
            source.conversation_id,
            source.sender_message_id,
            options.now(),
          ),
        ) !== null
      )
        return new WorkerStepStartResult({ started: false });
      const fate = await observe_conversation_operation(
        () =>
          system.send(source.conversation_id, text_message_content(CONTINUATION_PROMPT), {
            sender_label: LOOP_SENDER_LABEL,
            sender_message_id: source.sender_message_id,
            mode: 'queue',
          }),
        options.signal,
      );
      sent = true;
      if (fate instanceof PromptDeliveryRefused || fate instanceof PromptDeliveryUncertain) {
        database.write((tx) =>
          mark_continuation_error(
            tx,
            source,
            options.now(),
            fate instanceof PromptDeliveryUncertain
              ? 'continuation delivery uncertain'
              : `continuation refused (${fate.refusal_reason})`,
          ),
        );
      }
      if (fate instanceof PromptDeliveryRefused || fate instanceof PromptDeliverySuppressed)
        starts?.finish(source.sender_message_id);
      else if (fate instanceof PromptDeliveryUncertain) starts?.uncertain(source.sender_message_id);
      return WorkerStepStartResult.from_delivery_fate(fate);
    } catch (error) {
      if (error instanceof ConversationStartAborted) throw error;
      database.write((tx) =>
        mark_continuation_error(
          tx,
          source,
          options.now(),
          `continuation delivery failed: ${error instanceof Error ? error.message : String(error)}`,
        ),
      );
      return new WorkerStepStartResult({ started: false });
    } finally {
      if (!sent) starts?.finish_unbound(source.sender_message_id);
    }
  }, options.signal);
}
