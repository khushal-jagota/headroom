/** Ordered ownership risks that HTTP lists cannot pause at internal receipt barriers. */
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { afterEach, expect, test, vi } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import {
  PromptDeliveryInjected,
  PromptDeliveryQueued,
  PromptDeliveryStarted,
} from '../conversation/contracts.ts';
import { TurnEnded } from '../conversation/backends/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { Deferred } from '../conversation/runtime/state.ts';

const writers: ReturnType<typeof spawn>[] = [];
afterEach(async () => {
  await Promise.all(
    writers.splice(0).map(async (writer) => {
      if (writer.exitCode === null) {
        const exited = once(writer, 'exit');
        writer.kill();
        await exited;
      }
    }),
  );
});
function observe<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return Promise.race([
    promise,
    new Promise<T>((_resolve, reject) =>
      signal.addEventListener('abort', () => reject(signal.reason), { once: true }),
    ),
  ]);
}
function abort_observer(controller: AbortController): Error {
  const error = new Error('observer left');
  error.name = 'AbortError';
  controller.abort(error);
  return error;
}

// Inventory #18: kill discards its initial queue snapshot; a later admitted send is still owed a turn.
test('a send during native kill cancellation runs after the kill', async () => {
  const f = setup();
  await f.start();
  await f.send();
  const child = await f.child;
  await child.configure('cancel_running_turn', { barrier: 'cancel' });
  const killed = f.core.kill('core');
  await child.wait_for_barrier('cancel');
  expect(await f.send('after kill', 'later')).toBeInstanceOf(PromptDeliveryQueued);
  await child.release('cancel');
  await killed;
  await f.core.wait_until_quiescent();
  expect(f.children).toHaveLength(2);
  expect((await f.children[1].inspect()).delivered_text).toEqual(['after kill']);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'prompt',
  ]);
  await f.core.shutdown();
});

// Inventory #24: two controls name one held entry and only one can take it.
test('two promotions of one held id have one winner', async () => {
  const f = setup();
  await f.start();
  await f.send();
  await f.send('promote me', 'promoted');
  const identity = (await f.core.held_prompts('core'))[0].held_prompt_id;
  const results = await Promise.all([
    f.core.promote_held_prompt('core', identity, 'send_now'),
    f.core.promote_held_prompt('core', identity, 'send_now'),
  ]);
  expect(results.filter((result) => result instanceof PromptDeliveryStarted)).toHaveLength(1);
  expect(results.filter((result) => result === null)).toHaveLength(1);
  expect(
    (await (await f.child).inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(2);
  expect(
    f.store
      .read_events_after('core', 0)
      .filter(
        (row) => row.payload.kind === 'prompt' && row.payload.sender_message_id === 'promoted',
      ),
  ).toHaveLength(1);
  await f.core.shutdown();
});

// Inventory #34 backend case: only the observer leaves, not the owned native exchange.
test('observer departure during a native write retains one owned settlement', async () => {
  const f = setup({ behavior: { barrier: 'native' } });
  await f.start();
  const controller = new AbortController();
  const sent = f.send();
  const observed = observe(sent, controller.signal);
  const child = await f.child;
  await child.wait_for_barrier('native');
  const error = abort_observer(controller);
  await expect(observed).rejects.toBe(error);
  const duplicate = f.send();
  expect(f.core.conversations.get('core')!.settlement_tasks.size).toBeGreaterThan(0);
  await child.release('native');
  expect(await duplicate).toBeInstanceOf(PromptDeliveryStarted);
  expect(await sent).toBeInstanceOf(PromptDeliveryStarted);
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual(['prompt']);
  await f.core.shutdown();
});

// Inventories #31/#34 SQLite case/#37: accepted identity survives observer departure and a real writer lock.
// A child releases the SQLite lock independently. A pre-attempt BUSY barrier supplies a reachable
// async abort point; no assertion claims a timer can run while better-sqlite3 blocks this event loop.
test('accepted queued receipt and completion retain order behind a real writer lock', async () => {
  const f = setup();
  await f.start();
  await f.send();
  await f.send('held accepted', 'accepted');
  const child = await f.child;
  await child.configure('write_prompt', {
    barrier: 'next-receipt',
    events: [{ event: { kind: 'TurnEnded', values: { ending: 'completed' } } }],
  });
  await child.finish();
  await child.wait_for_barrier('next-receipt');
  const writer = spawn(
    'python3',
    [
      '-u',
      '-c',
      `import sqlite3,sys,time
connection=sqlite3.connect(sys.argv[1])
connection.execute('BEGIN IMMEDIATE')
print('locked',flush=True)
sys.stdin.readline()
time.sleep(0.15)
connection.rollback()
connection.close()
`,
      f.database_path,
    ],
    { stdio: ['pipe', 'pipe', 'pipe'] },
  );
  writers.push(writer);
  const lines = createInterface({ input: writer.stdout! });
  expect((await once(lines, 'line'))[0]).toBe('locked');
  const entered = new Deferred<void>();
  const original = f.store.append_delivered_prompt.bind(f.store);
  let first = true;
  vi.spyOn(f.store, 'append_delivered_prompt').mockImplementation((...args) => {
    if (first) {
      first = false;
      entered.resolve();
      throw Object.assign(new Error('pre-attempt writer contention'), { code: 'SQLITE_BUSY' });
    }
    return original(...args);
  });
  const sent = f.send('held accepted', 'accepted');
  const controller = new AbortController();
  const observed = observe(sent, controller.signal);
  await child.release('next-receipt');
  await entered.promise;
  const error = abort_observer(controller);
  await expect(observed).rejects.toBe(error);
  const duplicate = f.send('held accepted', 'accepted');
  writer.stdin!.end('release\n');
  expect(await duplicate).toBeInstanceOf(PromptDeliveryStarted);
  expect(await sent).toBeInstanceOf(PromptDeliveryStarted);
  await child.flush_events();
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'prompt',
    'turn_ended',
  ]);
  expect(
    (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(2);
  await f.core.shutdown();
});

// Inventories #32/#39: neither backend completion nor send_now replacement can overtake a steer receipt.
for (const replacement of ['backend ending', 'send_now'] as const) {
  test(`steer receipt precedes ${replacement}`, async () => {
    const retrying = new Deferred<void>();
    let available = false;
    let tracked_close = 0;
    const f = setup({
      core: {
        track_save_retry: () => ({
          set_detail() {},
          close() {
            tracked_close += 1;
          },
        }),
      },
    });
    await f.start();
    await f.send();
    const child = await f.child;
    const original = f.store.append_events.bind(f.store);
    vi.spyOn(f.store, 'append_events').mockImplementation((...args) => {
      if (
        args[1].some((payload) => payload.kind === 'prompt' && payload.mode === 'steer') &&
        !available
      ) {
        retrying.resolve();
        throw new Error('steer receipt unavailable');
      }
      return original(...args);
    });
    const steering = f.core.send('core', text_message_content('steer'), {
      sender_label: 'Owner',
      sender_message_id: 'steered',
      mode: 'steer',
    });
    await retrying.promise;
    let replaced: Promise<unknown>;
    if (replacement === 'backend ending')
      replaced = child.emit(new TurnEnded({ ending: 'completed' }));
    else
      replaced = f.core.send('core', text_message_content('replacement'), {
        sender_label: 'Owner',
        sender_message_id: 'replacement',
        mode: 'send_now',
      });
    expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual(['prompt']);
    available = true;
    expect(await steering).toBeInstanceOf(PromptDeliveryInjected);
    await replaced;
    await child.flush_events();
    await f.core.wait_until_quiescent();
    const rows = f.store.read_events_after('core', 0);
    expect(rows.slice(0, 3).map((row) => row.kind)).toEqual(['prompt', 'prompt', 'turn_ended']);
    expect(rows[1].payload.kind === 'prompt' && rows[1].payload.sender_message_id).toBe('steered');
    expect(tracked_close).toBe(1);
    if (replacement === 'send_now') expect(rows[3].kind).toBe('prompt');
    await f.core.shutdown();
  });
}

// Inventory #36: final metadata admission can fail after dequeue or promotion without losing guidance.
for (const delivery of ['queued', 'promoted'] as const) {
  test(`metadata retry retains ${delivery} guidance until one receipt`, async () => {
    const f = setup();
    await f.start();
    await f.send();
    await f.send('guidance', 'guidance');
    const retrying = new Deferred<void>();
    let available = false;
    const original = f.store.all_sender_messages_are_manager_notices.bind(f.store);
    vi.spyOn(f.store, 'all_sender_messages_are_manager_notices').mockImplementation((...args) => {
      if (!available) {
        retrying.resolve();
        throw new Error('metadata unavailable');
      }
      return original(...args);
    });
    const child = await f.child;
    const progressing =
      delivery === 'queued'
        ? child.finish()
        : f.core.promote_held_prompt(
            'core',
            (await f.core.held_prompts('core'))[0].held_prompt_id,
            'steer',
          );
    await retrying.promise;
    expect((await child.inspect()).delivered_text).toHaveLength(1);
    available = true;
    await progressing;
    await child.flush_events();
    await f.core.wait_until_quiescent();
    expect(
      f.store
        .read_events_after('core', 0)
        .filter(
          (row) => row.payload.kind === 'prompt' && row.payload.sender_message_id === 'guidance',
        ),
    ).toHaveLength(1);
    expect(
      f.store.read_events_after('core', 0).some((row) => row.kind === 'prompt_discarded'),
    ).toBe(false);
    await f.core.shutdown();
  });
}

// Inventory #38 adaptation: TS SQL is synchronous. Exercise the remaining post-read/pre-recheck
// admission interleave with a real competing send, without fabricating runtime sequence changes.
test('a post-read admission race cannot readmit one sender identity', async () => {
  const f = setup();
  await f.start();
  const original = f.store.sender_message_outcome.bind(f.store);
  let competitor: Promise<unknown> | null = null;
  let first = true;
  vi.spyOn(f.store, 'sender_message_outcome').mockImplementation((...args) => {
    const captured = original(...args);
    if (first) {
      first = false;
      competitor = f.send();
    }
    return captured;
  });
  expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
  expect(await competitor).toBeInstanceOf(PromptDeliveryStarted);
  expect(
    (await (await f.child).inspect()).calls.filter((call) => call.command === 'write_prompt'),
  ).toHaveLength(1);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual(['prompt']);
  await f.core.shutdown();
});
