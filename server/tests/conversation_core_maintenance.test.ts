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
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
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

// Inventory #51: a retiring child is still owned; one failed detach cannot hide
// another child or let an aborted stop count as proven native closure.
test('shutdown detaches retiring children concurrently and retains an unconfirmed child', async () => {
  const began = performance.now();
  const f = maintenance_setup();
  await f.start();
  await f.send('work');
  await f.core.start_conversation(
    new ConversationStartRequest({
      conversation_id: 'other',
      model: 'model',
      workspace_folder: '/workspace',
    }),
  );
  await f.core.send('other', text_message_content('other work'), { sender_label: 'Owner' });
  const [retiring, unconfirmed] = f.children;
  const state = f.core.conversations.get('core')!;
  const other = f.core.conversations.get('other')!;
  const token = other.running_turn!.token;
  const before = f.store.read_events_after('other', 0);
  const stop_entered = new Deferred<void>(),
    raw_stop = new Deferred<void>();
  const detach_entered = new Deferred<void>(),
    detach_release = new Deferred<void>();
  vi.spyOn(retiring, 'stop').mockImplementation(async () => {
    stop_entered.resolve();
    await raw_stop.promise;
    throw new Error('The original stop receipt failed after native detach');
  });
  const native_detach = retiring.detach.bind(retiring);
  const first_detach = vi.spyOn(retiring, 'detach').mockImplementation(async () => {
    detach_entered.resolve();
    await detach_release.promise;
    await native_detach();
    raw_stop.resolve();
  });
  const failed_detach = vi
    .spyOn(unconfirmed, 'detach')
    .mockRejectedValue(new Error('termination remains unconfirmed'));
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const discarding = f.core.discard_child(state, retiring);
  try {
    await stop_entered.promise;
    expect(state.child).toBeNull();
    const shutdown = f.core.shutdown({ deadline: performance.now() / 1000 + 0.05 });
    const expired = expect(shutdown).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
    await detach_entered.promise;
    expect(first_detach).toHaveBeenCalledOnce();
    expect(failed_detach).toHaveBeenCalledOnce();
    expect(await discarding).toBe(false);
    await expired;
    let quiescent = false;
    const actual = f.core.settled().finally(() => {
      quiescent = true;
    });
    void actual.catch(() => {});
    expect(quiescent).toBe(false);
    detach_release.resolve();
    await expect(actual).rejects.toThrow('Conversation core did not shut down cleanly');
    expect(other.child).toBe(unconfirmed);
    expect(other.child_is_quarantined).toBe(true);
    expect(other.backend_event_pump).toBeNull();
    await unconfirmed.emit(
      new ToolCallStarted({
        tool_call_id: 'late',
        title: 'Late',
        tool_kind: 'read',
      }),
      token,
    );
    await unconfirmed.emit(new TurnEnded({ ending: 'completed' }), token);
    await unconfirmed.flush_events();
    expect(f.store.read_events_after('other', 0)).toEqual(before);
    await expect(f.core.shutdown({ deadline: performance.now() / 1000 + 1 })).rejects.toThrow(
      'Conversation core did not shut down cleanly',
    );
  } finally {
    detach_release.resolve();
    raw_stop.resolve();
    await discarding;
    // The existing core fixture terminates its exact scripted children in afterEach.
  }
  console.info(
    `shutdown retiring-child connected proof: ${Math.round(performance.now() - began)} ms`,
  );
}, 10_000);
