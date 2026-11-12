/** Internal compaction and shutdown barriers that HTTP request lists cannot select. */
import { expect, test, vi } from 'vitest';
import { performance } from 'node:perf_hooks';
import { setup } from './conversation_core_fixture.ts';
import {
  ConversationStartRequest,
  PromptDeliveryQueued,
  PromptDeliveryStarted,
} from '../conversation/contracts.ts';
import {
  ContextCompacted,
  ToolCallStarted,
  TurnEnded,
} from '../conversation/backends/contracts.ts';
import { message_content_text, text_message_content } from '../conversation/message_content.ts';
import { Deferred } from '../conversation/runtime/state.ts';
function maintenance_setup() {
  let now = 100;
  const fixture = setup({
    record: { integer_now: () => now },
    core: { unix_time_now: () => now, monotonic_now: () => now },
  });
  return {
    ...fixture,
    start: () =>
      fixture.core.start_conversation(
        new ConversationStartRequest({
          conversation_id: 'core',
          model: 'model',
          workspace_folder: '/workspace',
        }),
      ),
    send: (text: string, identity?: string) =>
      fixture.core.send('core', text_message_content(text), {
        sender_label: 'Owner',
        sender_message_id: identity,
      }),
    advance: (seconds: number) => {
      now += seconds;
    },
  };
}

// Inventory #13: duplicate admission at the compaction boundary must remain one delivery.
test('boundary duplicate waits for compaction and runs once', async () => {
  const began = performance.now();
  const f = maintenance_setup();
  await f.start();
  expect(await f.send('first')).toBeInstanceOf(PromptDeliveryStarted);
  const child = f.children[0];
  await child.finish();
  await f.core.wait_until_quiescent();
  f.advance(50 * 60);
  expect(await f.send('at the boundary', 'same-id')).toBeInstanceOf(PromptDeliveryQueued);
  expect(await f.send('at the boundary', 'same-id')).toBeInstanceOf(PromptDeliveryQueued);
  expect((await child.inspect()).delivered_text).toEqual(['first', '/compact']);
  await child.emit(new ContextCompacted());
  await child.finish();
  await f.core.wait_until_quiescent();
  expect((await child.inspect()).delivered_text).toEqual(['first', '/compact', 'at the boundary']);
  const prompts = f.store
    .read_events_after('core', 0)
    .filter((row) => row.payload.kind === 'prompt');
  expect(
    prompts.filter(
      (row) => row.payload.kind === 'prompt' && row.payload.sender_message_id === 'same-id',
    ),
  ).toHaveLength(1);
  console.info(`compaction duplicate connected proof: ${Math.round(performance.now() - began)} ms`);
}, 10_000);

// Inventory #15: an accepted maintenance prompt must retry only its failed storage attempt.
test('accepted compaction retains its child and retries storage only', async () => {
  const began = performance.now();
  const f = maintenance_setup();
  await f.start();
  await f.send('first');
  const child = f.children[0];
  await child.finish();
  await f.core.wait_until_quiescent();
  f.advance(50 * 60);
  const real_append = f.store.append_delivered_prompt.bind(f.store);
  const failed = new Deferred<void>();
  let unavailable = true;
  let recorded = 0;
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(f.store, 'append_delivered_prompt').mockImplementation((id, options) => {
    const compaction = message_content_text(options.prompt.content) === '/compact';
    if (compaction && unavailable) {
      failed.resolve();
      throw Object.assign(new Error('record unavailable'), { code: 'SQLITE_IOERR' });
    }
    const rows = real_append(id, options);
    if (compaction) recorded += 1;
    return rows;
  });
  let settled = false;
  const sending = f.send('must stay held').then((fate) => {
    settled = true;
    return fate;
  });
  await failed.promise;
  expect((await child.inspect()).delivered_text).toEqual(['first', '/compact']);
  expect(f.children).toHaveLength(1);
  expect(settled).toBe(false);
  expect(recorded).toBe(0);
  expect(f.core.conversations.get('core')!.reserved_turn).not.toBeNull();
  unavailable = false;
  expect(await sending).toBeInstanceOf(PromptDeliveryQueued);
  await child.finish();
  await f.core.wait_until_quiescent();
  const snapshot = await child.inspect();
  expect(snapshot.delivered_text).toEqual(['first', '/compact', 'must stay held']);
  expect(
    snapshot.calls.some(
      (call) => call.command === 'cancel_running_turn' || call.command === 'stop',
    ),
  ).toBe(false);
  expect(recorded).toBe(1);
  console.info(
    `compaction save retry connected proof: ${Math.round(performance.now() - began)} ms`,
  );
}, 10_000);

// Inventory #51: failed or aborted detach never proves retirement or admits late callbacks.
test('shutdown retains an unconfirmed child and revokes its callbacks', async () => {
  const began = performance.now();
  for (const name of ['Error', 'AbortError']) {
    const f = maintenance_setup();
    await f.start();
    await f.send('work');
    const child = f.children[0];
    const state = f.core.conversations.get('core')!;
    const token = state.running_turn!.token;
    const before = f.store.read_events_after('core', 0);
    vi.spyOn(child, 'detach').mockRejectedValue(
      Object.assign(new Error('termination remains unconfirmed'), { name }),
    );
    vi.spyOn(console, 'error').mockImplementation(() => {});
    await f.core.shutdown();
    expect(state.child).toBe(child);
    expect(state.child_is_quarantined).toBe(true);
    expect(state.local_child_retirement_proven).toBe(false);
    expect(state.backend_event_pump).toBeNull();
    await child.emit(
      new ToolCallStarted({ tool_call_id: 'late', title: 'Late', tool_kind: 'read' }),
      token,
    );
    await child.emit(new TurnEnded({ ending: 'completed' }), token);
    await child.flush_events();
    expect(f.store.read_events_after('core', 0)).toEqual(before);
  }
  console.info(`shutdown callback connected proof: ${Math.round(performance.now() - began)} ms`);
}, 10_000);
