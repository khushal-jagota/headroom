/** Risk: queued recovery must remain pending until a committed delivery receipt exists.
 * Existing move tests finish synchronous sends and cannot prove later success or refusal.
 * Reuse the SQLite fixture and real record writer; no new browser/backend host. */
import { expect, test } from 'vitest';
import { fixture } from '../tests/ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import {
  ResolvedConversationStart,
  PromptDeliveryMode,
  PromptDeliveryRefusalReason,
} from '../conversation/contracts.ts';
import { PromptEventPayload, PromptDeliveryRefusedEventPayload } from '../conversation/events.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { observe_move_delivery } from './delivery_events.ts';
import { read_operations, write_operation } from './operations.ts';
import type { WorkMoveOperation } from './contracts.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';

test('queued moves finish only on committed recovery delivery, including later refusal', () => {
  const database = fixture();
  let reject = false;
  const store = new ConversationStore(database, {
    integer_now: () => 10,
    event_participant(tx, conversation_id, payloads) {
      observe_move_delivery(tx, conversation_id, payloads, 10000);
      return [];
    },
    transaction_participant() {
      if (reject) throw new Error('receipt rollback');
    },
  });
  for (const conversation_id of ['move-chief', 'move-manager']) {
    store.create_conversation(
      new ResolvedConversationStart({
        conversation_id,
        backend_key: parse_backend_instance_id('codex'),
        model: 'model',
        reasoning_effort: null,
        role_materials: null,
        workspace_folder: '/workspace',
        access: 'full',
      }),
    );
  }
  const operation: WorkMoveOperation = {
    operation_id: 'batch',
    machine_id: ORIGINAL_MAC_MACHINE_ID,
    everything: true,
    started_at_ms: 1000,
    finished_at_ms: null,
    items: [
      {
        owner: { kind: 'chief' },
        title: 'Chief',
        conversation_id: 'move-chief',
        phase: 'queued',
        phases: [{ phase: 'queued', started_at_ms: 1000, finished_at_ms: null }],
        result: null,
      },
      {
        owner: { kind: 'manager', id: 'si_proof' },
        title: 'Manager',
        conversation_id: 'move-manager',
        phase: 'queued',
        phases: [{ phase: 'queued', started_at_ms: 1000, finished_at_ms: null }],
        result: null,
      },
    ],
  };
  database.write((tx) => write_operation(tx, operation));
  const read = () => database.read(read_operations)[0]!;
  const prompt = new PromptEventPayload({
    content: text_message_content('Recover'),
    sender_label: 'machine move',
    sender_message_id: 'work-move:chief',
    mode: PromptDeliveryMode.queue,
  });
  reject = true;
  expect(() => store.append_events('move-chief', [prompt])).toThrow('receipt rollback');
  expect(read()).toEqual(operation);
  reject = false;
  store.append_events('move-chief', [
    new PromptEventPayload({ ...prompt, sender_message_id: 'ordinary' }),
  ]);
  expect(read().items[0]!.result).toBeNull();
  store.append_events('move-chief', [prompt]);
  expect(read().items[0]).toMatchObject({
    phase: 'complete',
    result: { moved: true, error: null },
  });
  expect(read().finished_at_ms).toBeNull();
  store.append_events('move-manager', [
    new PromptDeliveryRefusedEventPayload({
      ...prompt,
      sender_message_id: 'work-move:manager',
      refusal_reason: PromptDeliveryRefusalReason.no_such_conversation,
    }),
  ]);
  expect(read().items[1]).toMatchObject({
    phase: 'failed',
    result: { moved: true, error: expect.stringContaining('new conversation refused') },
  });
  expect(read().finished_at_ms).toBe(10000);
});
