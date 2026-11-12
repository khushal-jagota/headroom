import { setup } from './conversation_core_fixture.ts';
/** Internal ordering risks that HTTP request lists cannot pause at their exact boundary. */
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test, vi } from 'vitest';
import { fixture } from './ticket_fixture.ts';
import { ConversationStore } from '../conversation/record/data/store.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import {
  ConversationRoleMaterials,
  ConversationStartRequest,
  PromptDeliveryQueued,
  PromptDeliveryStarted,
  PromptDeliveryUncertain,
} from '../conversation/contracts.ts';
import {
  AgentMessageCompleted,
  TurnEnded,
  ToolCallStarted,
} from '../conversation/backends/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { Deferred } from '../conversation/runtime/state.ts';
import { ScriptedChild } from '../conversation/testing/scripted_child.ts';
import type { ScriptedBehavior } from '../conversation/testing/scripted_protocol.ts';
import type { ConversationStoreOptions } from '../conversation/record/contracts.ts';
import type { ConversationCoreOptions } from '../conversation/runtime/contracts.ts';

// Inventory #8: the provider reports news while its prompt receipt is still withheld.
// The real scripted process and real record together prove the provider/core/store boundary.
test('backend news waits for the accepted prompt receipt and prose stays transient', async () => {
  const f = setup({
    behavior: {
      barrier: 'receipt',
      events: [
        {
          event: {
            kind: 'ToolCallStarted',
            values: { tool_call_id: 'tool', title: 'Tool', tool_kind: 'read' },
          },
        },
        { event: { kind: 'TurnEnded', values: { ending: 'completed' } } },
      ],
    },
  });
  await f.start();
  const sent = f.send();
  const child = await f.child;
  await child.wait_for_barrier('receipt');
  expect(f.store.read_events_after('core', 0)).toEqual([]);
  const release = await f.core.conversations.get('core')!.lock.acquire();
  try {
    expect(await f.core.is_running('core')).toBe(false);
    expect(await f.core.has_pending_permission_ask('core')).toBe(false);
    expect(await f.core.has_pending_user_input('core')).toBe(false);
    expect(await f.core.is_running('unknown')).toBe(false);
    expect(f.core.conversations.has('unknown')).toBe(false);
  } finally {
    release();
  }
  await child.release('receipt');
  expect(await sent).toBeInstanceOf(PromptDeliveryStarted);
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'tool_call_started',
    'turn_ended',
  ]);
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(await f.core.is_running('core')).toBe(false);
  await f.core.shutdown();
});

// Inventory #11: possible emission must retain identity and must not send a retry.
test('possible emission stays uncertain and a duplicate never writes again', async () => {
  const f = setup({ behavior: { error: { name: 'PromptWriteFailed', message: 'receipt lost' } } });
  await f.start();
  expect(await f.send()).toBeInstanceOf(PromptDeliveryUncertain);
  expect(await f.send()).toBeInstanceOf(PromptDeliveryUncertain);
  const child = await f.child;
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt_delivery_uncertain',
  ]);
  expect(f.store.lifecycle_facts('core').find((fact) => fact.operation === 'prompt')?.status).toBe(
    'unknown',
  );
  expect(f.core.workspace_activity()).toBe('busy');
  await f.core.shutdown();
});

// Inventory #35: a record failure after native acceptance retries storage, not the provider.
// The same test exposes runtime prediction during commit and its rollback after rejection.
test('a failed save restores runtime and retains one accepted sender through storage retry', async () => {
  const rejected = new Deferred<void>();
  let first = true;
  let core: SqliteProcessConversationSystem;
  const f = setup({
    record: {
      transaction_participant: (_tx, candidate) => {
        if (candidate.receipt_sequences.length === 0) return;
        expect(core.runtime_turn('core')?.token.turn_number).toBe(1);
        if (first) {
          first = false;
          rejected.resolve();
          throw new Error('synthetic save failure');
        }
      },
    },
  });
  core = f.core;
  await f.start();
  const sent = f.send();
  await rejected.promise;
  expect(core.runtime_turn('core')).toBeNull();
  expect(f.store.read_events_after('core', 0)).toEqual([]);
  const duplicate = f.send();
  expect(await sent).toBeInstanceOf(PromptDeliveryStarted);
  expect(await duplicate).toBeInstanceOf(PromptDeliveryStarted);
  const child = await f.child;
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual(['prompt']);
  await core.shutdown();
});

// Inventory #33: a failed live-tail subscriber must not turn a committed receipt into a retry.
test('publication failure after receipt commit does not append or deliver twice', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const secondary = new Error('Ticket write failed');
  const f = setup({
    core: {
      publish_event: () => {
        throw new Error('subscriber failed');
      },
      on_held_model_change: () => {
        throw secondary;
      },
    },
  });
  await f.start();
  expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
  expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
  expect(
    await f.core.send('core', text_message_content('change'), {
      sender_label: 'Owner',
      sender_message_id: 'held-change',
      model_change: 'next',
    }),
  ).toBeInstanceOf(PromptDeliveryQueued);
  const child = await f.child;
  await child.finish();
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'model_changed',
    'prompt',
  ]);
  expect(
    await f.core.send('core', text_message_content('change'), {
      sender_label: 'Owner',
      sender_message_id: 'held-change',
      model_change: 'next',
    }),
  ).toBeInstanceOf(PromptDeliveryStarted);
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(2);
  expect(await f.core.is_running('core')).toBe(true);
  expect(f.core.conversations.get('core')!.reserved_turn).toBeNull();
  expect(f.core.conversations.get('core')!.sender_messages_being_delivered.size).toBe(0);
  expect(console.error).toHaveBeenCalledWith(
    'conversation core could not drain held prompts',
    secondary,
  );
  await child.emit(
    new ToolCallStarted({ tool_call_id: 'after-error', title: 'After error', tool_kind: 'read' }),
  );
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 4).map((row) => row.kind)).toEqual([
    'tool_call_started',
  ]);
  await f.core.shutdown();
});

// Inventory #7: an old turn's terminal event must not end its successor.
test('a turn ends once and its late terminal event does not end the next turn', async () => {
  const f = setup();
  await f.start();
  await f.send();
  const child = await f.child;
  const old = f.core.runtime_turn('core')!.token;
  await child.finish();
  await child.flush_events();
  await f.core.wait_until_quiescent();
  await f.send('two', 'second');
  await child.emit(new TurnEnded({ ending: 'completed' }), old);
  await child.emit(
    new AgentMessageCompleted({ content: text_message_content('not a notebook message') }),
    old,
  );
  await child.emit(
    new ToolCallStarted({ tool_call_id: 'stale', title: 'Stale', tool_kind: 'read' }),
    old,
  );
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.core.runtime_turn('core')?.token.turn_number).toBe(2);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'prompt',
  ]);
  await f.core.shutdown();
});

// Inventory #16: the queue entry disappears before its receipt, but sender identity must remain.
test('dequeued sender identity waits for the receipt instead of crossing the wire again', async () => {
  const f = setup();
  await f.start();
  await f.send();
  expect(await f.send('held', 'held-sender')).toBeInstanceOf(PromptDeliveryQueued);
  const child = await f.child;
  await child.configure('write_prompt', { barrier: 'queued-receipt' });
  await child.finish();
  await child.wait_for_barrier('queued-receipt');
  expect(await f.core.held_prompts('core')).toEqual([]);
  const duplicate = f.send('held', 'held-sender');
  await child.release('queued-receipt');
  expect(await duplicate).toBeInstanceOf(PromptDeliveryStarted);
  await f.core.wait_until_quiescent();
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(2);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'prompt',
  ]);
  await f.core.shutdown();
});
