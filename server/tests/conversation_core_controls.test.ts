/** Ordered ownership risks that HTTP lists cannot pause at internal receipt barriers. */
import { spawn } from 'node:child_process';
import { SHUTDOWN_GRACE_SECONDS } from '../lifecycle/configuration.ts';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { afterEach, expect, test, vi } from 'vitest';
import { setup } from './conversation_core_fixture.ts';
import {
  PromptDeliveryInjected,
  PromptDeliveryQueued,
  PromptDeliveryStarted,
  PromptDeliveryUncertain,
} from '../conversation/contracts.ts';
import { AgentMessageDelta, TurnEnded } from '../conversation/backends/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { ConversationRoleMaterials, ConversationStartRequest } from '../conversation/contracts.ts';
import { Deferred } from '../core/deferred.ts';
import { save_attempt } from '../conversation/runtime/save_attempt.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import { PROTOCOL_VERSION } from '../execution_configuration.ts';
import { native_wire_fixtures } from '../executor/testing/native_protocol_fixtures.ts';
import { create } from './ticket_fixture.ts';

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

// Recheck selects this Ticket's historical sources and requires explicit fresh lease evidence.
test('cleanup recheck refuses absent evidence and delegates only matching historical custody', async () => {
  const f = setup({ core: { unix_time_now: () => 100 } });
  const ticket = create(f.database);
  f.database.write((tx) =>
    tx.run('UPDATE tickets SET machine_id=? WHERE id=?', [ORIGINAL_MAC_MACHINE_ID, ticket.id]),
  );
  const lease = native_wire_fixtures().LeaseRequest;
  lease.identity.protocol_version = PROTOCOL_VERSION;
  lease.cleanup_uncertainties = [];
  const save = (value: unknown) =>
    f.database.write((tx) =>
      tx.run(
        'INSERT INTO mac_executor_presence(machine_id,executor_incarnation,lease_json,last_lease_at_ms) VALUES(?,?,?,100000) ON CONFLICT(machine_id) DO UPDATE SET lease_json=excluded.lease_json',
        [ORIGINAL_MAC_MACHINE_ID, lease.identity.executor_incarnation, JSON.stringify(value)],
      ),
    );
  save(lease);
  expect((await f.core.recheck_worker_cleanup(ticket.id)).complete).toBe(true);
  const { cleanup_uncertainties: _evidence, ...missing } = lease;
  save(missing);
  expect(await f.core.recheck_worker_cleanup(ticket.id)).toMatchObject({
    complete: false,
    reason: 'Fresh executor cleanup evidence is unavailable',
  });
  const source = {
    executor_incarnation: lease.identity.executor_incarnation,
    child_id: 'past-child',
    conversation_id: 'past-conversation',
  };
  f.database.write((tx) => {
    for (const reference of [
      source,
      { ...source, child_id: 'other-child', conversation_id: 'other-conversation' },
    ])
      tx.run(
        "INSERT INTO executor_child_assignments(executor_incarnation,child_id,conversation_id,principal_kind,principal_id,created_at_ms) VALUES(?,?,?,'ticket',?,0)",
        [reference.executor_incarnation, reference.child_id, reference.conversation_id, ticket.id],
      );
  });
  lease.cleanup_uncertainties = [
    { ticket_id: ticket.id, conversation_id: source.conversation_id, reason: 'Unproven cleanup' },
  ];
  save(lease);
  const recheck = vi.fn(async () => ({ complete: false, reason: 'Still alive', scope: 'fixture' }));
  f.core.configure_worker_cleanup_recheck(recheck);
  expect((await f.core.recheck_worker_cleanup(ticket.id)).complete).toBe(false);
  expect(recheck).toHaveBeenCalledExactlyOnceWith(source, ticket.id);
  // A successful answer about the old assignment cannot prove a replacement source.
  recheck.mockImplementation(async () => {
    f.database.write((tx) =>
      tx.run('UPDATE tickets SET machine_id=? WHERE id=?', [HOME_MACHINE_ID, ticket.id]),
    );
    return { complete: true, reason: 'Old source ended', scope: 'fixture' };
  });
  expect(await f.core.recheck_worker_cleanup(ticket.id)).toMatchObject({
    complete: false,
    reason: 'The Worker source changed during cleanup inspection',
  });
  expect(f.children).toHaveLength(0);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// A workspace cannot detach on uncertain cleanup, or lose its fence when a caller leaves.
test('workspace retirement keeps exact child and detach barriers until actual completion', async () => {
  const f = setup();
  await f.start();
  vi.spyOn(f.store, 'workspace_conversation_ids').mockResolvedValue(['core']);
  const inspection = vi.spyOn(f.store, 'settle_recorded_source').mockResolvedValue(null);
  f.store.blocked_local_conversations.add('core');
  vi.useFakeTimers();
  f.core.defer_start_drain(f.core.conversations.get('core')!);
  const identity = { machine_id: HOME_MACHINE_ID, workspace_id: 'private-workspace' };
  const detach = vi.fn(async () => ({ path: '/detached' }));
  const cleanup = { retire_local_children: f.core.retire_local_workspace };
  expect(await cleanup.retire_local_children('ticket', identity, detach)).toEqual({
    reason: 'Local child retirement failed',
  });
  expect(detach).not.toHaveBeenCalled();
  inspection.mockRestore();
  f.store.blocked_local_conversations.clear();
  f.core.conversations.get('core')!.child_is_quarantined = false;
  const drain = vi.spyOn(f.core, 'drain_held_prompts');
  await vi.advanceTimersByTimeAsync(1000);
  expect(drain).not.toHaveBeenCalled();
  drain.mockRestore();
  vi.useRealTimers();
  const started = new Deferred<void>();
  const detached = new Deferred<{ path: string }>();
  const retiring = cleanup.retire_local_children('ticket', identity, () => {
    started.resolve();
    return detached.promise;
  });
  await started.promise;
  const observer = new AbortController();
  const departed = observe(retiring, observer.signal);
  const reason = abort_observer(observer);
  await expect(departed).rejects.toBe(reason);
  expect(f.core.workspace_admission.active).toBe(true);
  await expect(f.send()).rejects.toThrow('Ticket workspace admission is closed for retirement');
  expect(await f.core.retire_local_workspace('ticket', identity, detach)).toEqual({
    reason: 'Ticket workspace retirement is already in progress',
  });
  detached.resolve({ path: '/detached' });
  expect(await retiring).toEqual({ path: '/detached' });
  expect(f.core.workspace_admission.active).toBe(false);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

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
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
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
  const child = await f.child;
  for (const fate of ['accepted', 'refused', 'uncertain'] as const) {
    const id = `${fate}-promotion`;
    f.core.start_admission.try_start(await f.core.automatic_start_request(id, 'core'));
    if (fate === 'refused')
      expect(await f.send('ahead', 'ahead')).toBeInstanceOf(PromptDeliveryQueued);
    expect(await f.send('promote steer', id)).toBeInstanceOf(PromptDeliveryQueued);
    if (fate === 'refused')
      expect(await f.send('behind', 'behind')).toBeInstanceOf(PromptDeliveryQueued);
    const held = (await f.core.held_prompts('core')).find(
      (entry) => entry.sender_message_id === id,
    )!;
    await child.configure('steer', {
      result: {
        fate,
        ...(fate === 'refused'
          ? { refusal_reason: 'backend_cannot_steer', refusal_detail: 'turn is compacting' }
          : {}),
      },
    });
    const logged = vi.spyOn(console, 'error').mockImplementation(() => {});
    const promoted = await f.core.promote_held_prompt('core', held.held_prompt_id, 'steer');
    const logged_lines = logged.mock.calls.map(([line]) => String(line));
    logged.mockRestore();
    expect(promoted).toBeInstanceOf(
      fate === 'refused'
        ? PromptDeliveryQueued
        : fate === 'uncertain'
          ? PromptDeliveryUncertain
          : PromptDeliveryInjected,
    );
    if (fate === 'refused') {
      // A refused promotion was not delivered. It keeps its place, identity and admission.
      expect(promoted).toEqual(new PromptDeliveryQueued({ queue_position: 2 }));
      expect(
        (await f.core.held_prompts('core')).map((entry) => [
          entry.held_prompt_id === held.held_prompt_id,
          entry.sender_message_id,
          entry.queue_reason,
        ]),
      ).toEqual([
        [false, 'ahead', 'requested'],
        [true, id, 'steer_refused'],
        [false, 'behind', 'requested'],
      ]);
      expect(f.core.start_admission.has_requests([id])).toBe(true);
      expect(
        f.store.read_events_after('core', 0).some((row) => row.kind === 'prompt_delivery_refused'),
      ).toBe(false);
      // The refusal is not recorded, so the backend's own text reaches the log once.
      expect(logged_lines).toEqual([
        expect.stringMatching(/error_class=BackendSteerRefused error="turn is compacting"$/),
      ]);
      await child.finish();
      await f.core.wait_until_quiescent();
      expect(await f.core.held_prompts('core')).toEqual([]);
      expect(
        f.store
          .read_events_after('core', 0)
          .filter((row) => row.payload.kind === 'prompt' && row.payload.sender_message_id === id),
      ).toHaveLength(1);
      // The uncertain case needs a running turn that has already shown activity.
      await child.emit(new AgentMessageDelta({ text_delta: 'redelivered activity' }));
      await child.flush_events();
      continue;
    }
    expect(f.core.start_admission.has_requests([id])).toBe(fate === 'uncertain');
    expect(
      f.database.read((sql) =>
        sql.all('SELECT identity FROM automatic_start_bindings WHERE identity=?', [id]),
      ),
    ).toEqual(fate === 'uncertain' ? [{ identity: id }] : []);
    if (fate === 'uncertain') {
      await child.emit(new AgentMessageDelta({ text_delta: 'later activity' }));
      await child.flush_events();
      // Python caches acceptance per turn. A later uncertain steer stays bound
      // until the exact child retires, even when that turn emits more activity.
      expect(f.core.start_admission.has_requests([id])).toBe(true);
      expect(
        f.database.read((sql) =>
          sql.all('SELECT identity FROM automatic_start_bindings WHERE identity=?', [id]),
        ),
      ).toEqual([{ identity: id }]);
      await f.core.kill('core');
      expect(f.core.start_admission.has_requests([id])).toBe(false);
      expect(
        f.database.read((sql) =>
          sql.all('SELECT identity FROM automatic_start_bindings WHERE identity=?', [id]),
        ),
      ).toEqual([]);
    }
  }
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Inventory #34 backend case: only the observer leaves, not the owned native exchange.
test('observer departure during a native write retains one owned settlement', async () => {
  const f = setup({ behavior: { barrier: 'native' } });
  await f.start();
  // The retained domain seam uses BUSY-only retry. Semantic failures must roll back once.
  const rejected_domain = new Error('domain rejected');
  let rejected_calls = 0;
  await expect(
    f.core.record_events_transaction('core', (tx) => {
      rejected_calls += 1;
      tx.run("UPDATE projects SET name='rejected' WHERE id='project_other'");
      throw rejected_domain;
    }),
  ).rejects.toBe(rejected_domain);
  expect(rejected_calls).toBe(1);
  expect(
    f.database.read(
      (sql) =>
        sql.get<{ name: string }>("SELECT name FROM projects WHERE id='project_other'")?.name,
    ),
  ).toBe('Other');
  const original_transaction = f.store.transact_events.bind(f.store);
  let transaction_attempts = 0;
  const busy = vi.spyOn(f.store, 'transact_events').mockImplementation((...args) => {
    transaction_attempts += 1;
    if (transaction_attempts === 1)
      throw Object.assign(new Error('writer contention'), { code: 'SQLITE_BUSY' });
    return original_transaction(...args);
  });
  let committed_calls = 0;
  expect(
    await f.core.record_events_transaction('core', (tx) => {
      committed_calls += 1;
      tx.run("UPDATE projects SET name='committed' WHERE id='project_other'");
      return { value: 'once', payloads: [] };
    }),
  ).toBe('once');
  expect(transaction_attempts).toBe(2);
  expect(committed_calls).toBe(1);
  busy.mockRestore();
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
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
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
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Inventories #32/#39: neither backend completion nor send_now replacement can overtake a steer receipt.
for (const replacement of ['backend ending', 'send_now'] as const) {
  test(`steer receipt precedes ${replacement}`, async () => {
    const retrying = new Deferred<void>();
    let available = false;
    let tracked_close = 0;
    const f = setup({
      core: {
        wait_registry: {
          track: () => ({
            set_detail() {},
            close() {
              tracked_close += 1;
            },
          }),
        },
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
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
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
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
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
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// New record option risk: shutdown must cancel generic backoff promptly without another
// failed save attempt. Existing BUSY and observer cases do not reach this outer retry wait.
test('shutdown retains an active BUSY save and cancels generic backoff', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const failed = new Deferred<void>();
  let attempts = 0;
  let closed = 0;
  const set_detail = vi.fn();
  const track = vi.fn(() => ({
    set_detail,
    close() {
      closed += 1;
    },
  }));
  const f = setup({ core: { wait_registry: { track } } });
  await f.start();
  const busy_shutdown = new AbortController();
  let busy_attempts = 0;
  const retained = f.store.settle_recording(
    'core',
    () => {
      busy_attempts += 1;
      if (busy_attempts === 1) {
        busy_shutdown.abort();
        throw Object.assign(new Error('writer contention'), { code: 'SQLITE_BUSY' });
      }
      return f.store.advance_owner_read_through_sequence('core', 0);
    },
    { shutdown_signal: busy_shutdown.signal },
  );
  expect((await retained)?.conversation_id).toBe('core');
  expect(busy_attempts).toBe(2);
  vi.spyOn(f.store, 'append_delivered_prompt').mockImplementation(() => {
    attempts += 1;
    if (attempts === 2) failed.resolve();
    throw Object.assign(new Error('record unavailable'), { code: 'SQLITE_IOERR' });
  });
  const sent = f.send();
  const rejected = expect(sent).rejects.toMatchObject({ name: 'AbortError' });
  await failed.promise;
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  await rejected;
  expect(attempts).toBe(2);
  expect(closed).toBe(1);
  expect(set_detail).toHaveBeenCalledExactlyOnceWith('Stuck at write 2');
  expect(track).toHaveBeenCalledExactlyOnceWith('vps_save_retrying', 'core', {
    detail: 'Stuck at write 1',
    conversation_id: 'core',
  });
  expect(f.store.read_events_after('core', 0)).toEqual([]);
});

// Update preparation must retain final native output while the save itself retries.
test('update retirement retains final callbacks through the save cut', async () => {
  let hold_write = false;
  const attempted = new Deferred<void>();
  const f = setup({
    core: {
      save_attempt: (id, work, mutations) => {
        if (hold_write) {
          attempted.resolve();
          throw Object.assign(new Error('synthetic writer contention'), { code: 'SQLITE_BUSY' });
        }
        return save_attempt(id, work, mutations);
      },
    },
  });
  await f.start();
  await f.send();
  const child = await f.child;
  await child.configure('stop', {
    barrier: 'update-stop',
    events: [{ event: { kind: 'CommandReplyReported', values: { reply_text: 'saved at stop' } } }],
  });
  hold_write = true;
  let ready = false;
  const prepared = f.core.prepare_update('now').then((value) => {
    ready = value;
    return value;
  });
  await child.wait_for_barrier('update-stop');
  await attempted.promise;
  await child.release('update-stop');
  expect(f.core.workspace_admission.starts_closed).toBe(true);
  expect(f.core.workspace_admission.closed).toBe(false);
  expect(ready).toBe(false);
  await expect(f.send('refused start', 'refused')).rejects.toThrow('Workspace admission is closed');
  hold_write = false;
  expect(await prepared).toBe(true);
  const events = f.store.read_events_after('core', 0);
  expect(events.map((row) => row.kind)).toEqual(['prompt', 'command_reply', 'turn_ended']);
  expect(events[1].payload).toMatchObject({ reply_text: 'saved at stop' });
  expect(f.core.workspace_admission.closed).toBe(true);
  f.core.cancel_update();
  expect(await f.send('after update failure', 'next')).toBeInstanceOf(PromptDeliveryStarted);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// Cancel must invalidate the old preparation without dropping newly admitted messages.
test('Cancel retains late retirement and does not authorize its old snapshot', async () => {
  const f = setup();
  await f.start();
  await f.send();
  const child = await f.child;
  await child.configure('stop', { barrier: 'canceled-update-stop' });
  const old_preparation = f.core.prepare_update('now');
  await child.wait_for_barrier('canceled-update-stop');
  expect(f.core.cancel_update()).toBe(true);
  expect(await f.send('after Cancel', 'new-work')).toBeInstanceOf(PromptDeliveryQueued);
  // A retry cannot take the output cut while the canceled stop still owns its child.
  expect(await f.core.prepare_update('now')).toBe(false);
  f.core.cancel_update();
  await child.release('canceled-update-stop');
  expect(await old_preparation).toBe(false);
  await f.core.wait_until_quiescent();
  expect(f.children).toHaveLength(2);
  expect((await f.children[1].inspect()).delivered_text).toEqual(['after Cancel']);
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt',
    'turn_ended',
    'prompt',
  ]);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// A timed-out observer leaves actual retirement owned without authorizing its old write cut.
test('an aborted update observation cannot close writes after its retirement settles', async () => {
  const f = setup();
  await f.start();
  await f.send();
  const child = await f.child;
  await child.configure('stop', { barrier: 'expired-update-stop' });
  const observation = new AbortController();
  const expired = f.core.prepare_update('now', observation.signal);
  await child.wait_for_barrier('expired-update-stop');
  observation.abort();
  expect(await expired).toBe(false);
  expect(f.core.workspace_admission.closed).toBe(false);
  await child.release('expired-update-stop');
  expect(f.core.workspace_admission.starts_closed).toBe(true);
  expect(f.core.workspace_admission.closed).toBe(false);
  expect(await f.core.prepare_update('now')).toBe(true);
  expect(f.core.workspace_admission.closed).toBe(true);
  f.core.cancel_update();
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// A deferred replacement must preserve its incumbent before either destructive control.
// The same existing scripted child proves eventual queue drain after admission opens.
test('unadmitted send_now and promotion preserve the running turn then drain once', async () => {
  let now = 0;
  const f = setup({ core: { monotonic_now: () => now } });
  try {
    await f.start();
    await f.send();
    const child = await f.child;
    const running = f.core.runtime_turn('core');
    expect(
      f.core.start_admission.try_start(await f.core.automatic_start_request('other-start', 'core')),
    ).toBe(true);
    expect(
      f.core.start_admission.try_start(await f.core.automatic_start_request('later', 'core')),
    ).toBe(false);
    expect(
      await f.core.send('core', text_message_content('later'), {
        sender_label: 'loop',
        sender_message_id: 'later',
        mode: 'send_now',
      }),
    ).toBeInstanceOf(PromptDeliveryQueued);
    const held = (await f.core.held_prompts('core'))[0];
    expect(
      await f.core.promote_held_prompt('core', held.held_prompt_id, 'send_now'),
    ).toBeInstanceOf(PromptDeliveryQueued);
    expect(f.core.runtime_turn('core')).toBe(running);
    expect(f.core.conversations.get('core')!.phase).toBe('running');
    expect(
      (await child.inspect()).calls.filter((call) => call.command === 'cancel_running_turn'),
    ).toEqual([]);
    await f.core.drain_held_prompts(f.core.conversations.get('core')!);
    expect(
      f.core.start_admission.snapshots().find((entry) => entry.request.identity === 'later')?.state,
    ).toBe('queued');
    await child.finish();
    await f.core.wait_until_quiescent();
    expect(f.core.conversations.get('core')!.phase).toBe('idle');
    // With no running turn, a steer promotion is refused at once. The message keeps its place.
    expect(await f.core.promote_held_prompt('core', held.held_prompt_id, 'steer')).toEqual(
      new PromptDeliveryQueued({ queue_position: 1 }),
    );
    expect(
      (await f.core.held_prompts('core')).map((entry) => [
        entry.held_prompt_id,
        entry.queue_reason,
      ]),
    ).toEqual([[held.held_prompt_id, 'steer_refused']]);
    expect(f.core.start_admission.has_requests(['later'])).toBe(true);
    expect(
      await f.core.send('core', text_message_content('manual work'), {
        sender_label: 'owner',
        sender_message_id: 'manual',
        mode: 'send_now',
      }),
    ).toBeInstanceOf(PromptDeliveryStarted);
    // Entering a manual turn immediately removes the deferred head's eligibility.
    expect(
      f.core.start_admission.snapshots().find((entry) => entry.request.identity === 'later')?.state,
    ).toBe('queued');
    now = 1;
    f.core.start_admission.finish('other-start');
    await child.finish();
    await f.core.wait_until_quiescent();
    expect((await child.inspect()).delivered_text.at(-1)).toBe('later');
    expect(
      (await child.inspect()).calls.filter((call) => call.command === 'write_prompt'),
    ).toHaveLength(3);
    expect(await f.core.held_prompts('core')).toEqual([]);
    expect(
      f.store
        .read_events_after('core', 0)
        .filter(
          (row) =>
            'sender_message_id' in row.payload &&
            row.payload.sender_message_id === 'later' &&
            ['prompt', 'prompt_delivery_refused'].includes(row.kind),
        )
        .map((row) => row.kind),
    ).toEqual(['prompt']);
  } finally {
    await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  }
});

// A failed pre-queue callback stays unsafe after its settled promise leaves the active set.
test('a settled report rejection still refuses the later update write cut', async () => {
  const f = setup();
  await f.start();
  await f.send();
  await f.core.wait_until_quiescent();
  const state = f.core.conversations.get('core')!;
  const failure = new Error('acceptance could not save');
  vi.spyOn(f.core, 'accept_automatic_start').mockRejectedValueOnce(failure);
  await expect(
    state.child_event_sink!.report(
      state.running_turn!.token,
      new AgentMessageDelta({ text_delta: 'unsaved final output' }),
    ),
  ).rejects.toBe(failure);
  expect(state.settlement_tasks.size).toBe(0);
  await expect(f.core.prepare_update('now')).rejects.toThrow('A backend result did not settle');
  expect(f.core.workspace_admission.closed).toBe(false);
  f.core.cancel_update();
  expect(await f.core.prepare_update('now')).toBe(true);
  f.core.cancel_update();
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// The old core stays cached after unlink. Its lock must fence a send admitted during
// the finish callback; an HTTP restart response cannot expose this interleaving.
test('restart holds source proof through unlink and fences the old prompt queue', async () => {
  const f = setup();
  const ticket = create(f.database);
  await f.start();
  f.database.write((tx) =>
    tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', ['core', ticket.id]),
  );
  await f.send();
  const writes = vi.spyOn(f.children[0], 'write_prompt');
  const finishing = new Deferred<void>();
  const release = new Deferred<void>();
  const restarted = f.core.kill_for_restart('core', ticket.id, async (check) => {
    expect(f.core.conversations.get('core')!.lock.locked).toBe(true);
    finishing.resolve();
    await release.promise;
    f.database.write((tx) => {
      check?.(tx);
      tx.run('UPDATE tickets SET conversation_id=NULL WHERE id=?', [ticket.id]);
    });
  });
  await finishing.promise;
  const late = f.send('must not run', 'late').then(
    () => null,
    (error) => error as Error,
  );
  release.resolve();
  await restarted;
  expect(await late).toMatchObject({ message: 'This Worker conversation was restarted' });
  expect(f.core.conversations.get('core')!.held_prompts).toHaveLength(0);
  expect(f.children).toHaveLength(1);
  expect(writes).not.toHaveBeenCalled();
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
});

// A permanent save error used to retry forever and hold the conversation. It must give up,
// end the turn as failed, stop the child, and refuse new work, with an alarm.
test('a permanent save error gives up, fails the turn, stops the child, and refuses work', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const track = vi.fn(() => ({ set_detail() {}, close() {} }));
  const f = setup({ core: { wait_registry: { track } } });
  await f.start();
  expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
  const child = await f.child;
  let attempts = 0;
  vi.spyOn(f.store, 'commit_backend_event').mockImplementation(() => {
    attempts += 1;
    throw Object.assign(new Error('synthetic constraint'), { code: 'SQLITE_CONSTRAINT_UNIQUE' });
  });
  await child.finish();
  await vi.waitFor(() => expect(f.core.runtime_turn('core')).toBeNull(), { timeout: 10_000 });
  await f.core.wait_until_quiescent();
  expect(attempts).toBe(4);
  const rows = f.store.read_events_after('core', 0);
  expect(rows.map((row) => row.kind)).toEqual(['prompt', 'turn_ended']);
  expect(rows[1].payload).toMatchObject({
    ending: 'failed',
    error_summary: expect.stringContaining('could not save this conversation'),
  });
  // The stopped child has closed its scripted process.
  await expect(child.inspect()).rejects.toThrow('scripted child is closed');
  expect(f.core.conversations.get('core')!.child).toBeNull();
  expect(track).toHaveBeenCalledWith('vps_save_failed', 'core', {
    detail: 'Save error SQLITE_CONSTRAINT_UNIQUE',
    conversation_id: 'core',
  });
  await expect(f.send('two', 'second')).rejects.toMatchObject({
    code: 'conversation_save_failed',
  });
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
}, 15_000);

// At-most-once delivery: the agent received this prompt, but its receipt save gave up. The
// sender must hear "uncertain", not an error that reads as "not sent", and a restart must
// not send the prompt again.
test('a receipt save that gives up reports uncertain and is not resent after a restart', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const f = setup();
  await f.start();
  vi.spyOn(f.store, 'append_delivered_prompt').mockImplementation(() => {
    throw Object.assign(new Error('synthetic constraint'), { code: 'SQLITE_CONSTRAINT_CHECK' });
  });
  expect(await f.send('one')).toBeInstanceOf(PromptDeliveryUncertain);
  const child = await f.child;
  await f.core.wait_until_quiescent();
  expect(f.store.read_events_after('core', 0).map((row) => row.kind)).toEqual([
    'prompt_delivery_uncertain',
  ]);
  // The stopped child's final snapshot holds the one delivery.
  await expect(child.inspect()).rejects.toThrow('scripted child is closed');
  expect(child.last_observation?.delivered_text).toEqual(['Role\n\none']);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
  const restarted = setup({ restart_of: f.database });
  // Restart recovery reads this: the prompt stays unknown, so nothing replays it.
  expect(
    restarted.store
      .lifecycle_facts('core')
      .filter((fact) => fact.kind === 'attempt' && fact.operation === 'prompt')
      .map((fact) => fact.status),
  ).toEqual(['unknown']);
  // The next ordinary send resumes eligible held messages. The uncertain one is not among them.
  expect(await restarted.core.held_prompts('core')).toEqual([]);
  expect(await restarted.send('two', 'second')).toBeInstanceOf(PromptDeliveryStarted);
  const next = await restarted.child;
  await next.finish();
  await restarted.core.wait_until_quiescent();
  expect((await next.inspect()).delivered_text).toEqual(['Role\n\ntwo']);
  await restarted.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
}, 15_000);

// The same rule for a steer: the agent can hold it, so a save that gave up reports uncertain.
test('a steer receipt save that gives up reports uncertain', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const f = setup();
  await f.start();
  await f.send();
  const original = f.store.append_events.bind(f.store);
  vi.spyOn(f.store, 'append_events').mockImplementation((...args) => {
    if (args[1].some((payload) => payload.kind === 'prompt' && payload.mode === 'steer'))
      throw new Error('synthetic error inside the write');
    return original(...args);
  });
  const steering = f.core.send('core', text_message_content('steer'), {
    sender_label: 'Owner',
    sender_message_id: 'steered',
    mode: 'steer',
  });
  expect(await steering).toBeInstanceOf(PromptDeliveryUncertain);
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
}, 15_000);

// A corrupt database stops new work across the process at once, with one alarm. The database
// fails while the prompt is being written, so every later save of this delivery refuses: the
// sender must still hear "uncertain", never "not sent".
test('an unusable database fails the conversation at once and refuses new work', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const track = vi.fn(() => ({ set_detail() {}, close() {} }));
  const f = setup({
    core: { wait_registry: { track } },
    behavior: { barrier: 'write', error: { name: 'PromptWriteFailed', message: 'synthetic' } },
  });
  await f.start();
  const sending = f.send('one');
  const child = await f.child;
  await child.wait_for_barrier('write');
  const corrupt = Object.assign(new Error('synthetic corruption'), { code: 'SQLITE_CORRUPT' });
  await f.store
    .settle_recording('elsewhere', () => {
      throw corrupt;
    })
    .catch(() => {});
  await child.release('write');
  expect(await sending).toBeInstanceOf(PromptDeliveryUncertain);
  expect(track).toHaveBeenCalledWith('vps_save_failed', 'database', {
    detail: 'Save error SQLITE_CORRUPT',
    conversation_id: null,
  });
  await expect(
    f.core.start_conversation(
      new ConversationStartRequest({
        conversation_id: 'other',
        model: 'model',
        workspace_folder: '/workspace',
        role_materials: new ConversationRoleMaterials({ role_text: 'Role' }),
      }),
    ),
  ).rejects.toMatchObject({ code: 'conversation_save_failed' });
  await f.core.wait_until_quiescent();
  expect(f.core.conversations.get('core')!.phase).toBe('idle');
  // The child process stopped, but the unusable database cannot record that, so shutdown fails.
  await expect(child.inspect()).rejects.toThrow('scripted child is closed');
  await expect(
    f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS }),
  ).rejects.toThrow('did not shut down cleanly');
});

// Send now cancels the running turn, then its turn reservation save gives up. The promoted
// message must be held again, and the phase must settle, or a later stop waits forever.
test('a reservation save that gives up after a cancel holds the message and still stops', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  const f = setup();
  await f.start();
  expect(await f.send()).toBeInstanceOf(PromptDeliveryStarted);
  expect(await f.send('two', 'second')).toBeInstanceOf(PromptDeliveryQueued);
  const [held] = await f.core.held_prompts('core');
  vi.spyOn(f.store, 'reserve_turn').mockImplementation(() => {
    throw Object.assign(new Error('synthetic constraint'), { code: 'SQLITE_CONSTRAINT_UNIQUE' });
  });
  await expect(
    f.core.promote_held_prompt('core', held.held_prompt_id, 'send_now'),
  ).rejects.toMatchObject({ code: 'SQLITE_CONSTRAINT_UNIQUE' });
  const state = f.core.conversations.get('core')!;
  expect((await f.core.held_prompts('core')).map((entry) => entry.held_prompt_id)).toEqual([
    held.held_prompt_id,
  ]);
  expect(state.sender_messages_being_delivered.size).toBe(0);
  // The failed conversation stops its child. A stop then completes instead of waiting forever.
  await f.core.wait_until_quiescent();
  await f.core.kill('core');
  expect(state.phase).toBe('idle');
  await f.core.shutdown({ deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS });
}, 15_000);
