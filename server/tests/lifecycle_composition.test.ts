/** Gap: individual loop tests do not prove shared ownership, startup order, or
 * late-transport fencing at lifecycle shutdown. Reuse the Ticket DB and locks.
 */
import { afterEach, expect, test, vi } from 'vitest';
import { create, fixture, fixture_path, now, read } from './ticket_fixture.ts';
import { createDatabase } from '../core/database/index.ts';
import { record_attention } from '../notifications/attention.ts';
import { StandardsWebPushAdapter } from '../notifications/web_push.ts';
import { ChangeSignal } from '../core/changes/index.ts';
import { loadConfig } from '../core/config.ts';
import { TestClock } from '../core/clock.ts';
import { acquireLock, acquireRuntimeOwnership } from '../core/locks/index.ts';
import type { OwnedAreaContext } from '../core/authority/register.ts';
import type { BackgroundServiceFactory } from '../lifecycle/contracts.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import {
  compose_background_services,
  dispatcher_admitted,
  production_notification_delivery,
} from '../lifecycle/background.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import { ManagerWakeLoop } from '../manager_wakes/runtime.ts';
import { NotificationLoop } from '../notifications/runtime.ts';
import type { APNsResult, WebPushResult } from '../notifications/contracts.ts';
import * as notificationData from '../notifications/data.ts';
import * as apnsData from '../notifications/apns_data.ts';
import { startServer } from '../core/start.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { PromptDeliveryStarted } from '../conversation/contracts.ts';
import { WorkerStepReadinessLoop } from '../runtime/worker_step_readiness_loop.ts';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { configure_conversation_start } from '../runtime/conversation_start.ts';
import * as wakeData from '../manager_wakes/data.ts';
import { HttpRequestScope } from '../core/http/lifetime.ts';

const next_turn = () => new Promise<void>((resolve) => setImmediate(resolve));

// A combined HTTP host can hide one untracked path behind another held path.
// Independent gates prove each actual lifetime without another connected host.
test('HTTP lifetime independently retains a handler and a pending iterator return', async () => {
  const began = performance.now();
  const scope = new HttpRequestScope();
  const handler = done<void>();
  const handled = scope.run(() => handler.promise);
  let handler_done = false;
  const handler_drain = scope.settled().then(() => {
    handler_done = true;
  });
  await next_turn();
  expect(handler_done).toBe(false);
  handler.resolve();
  await Promise.all([handled, handler_drain]);
  const entered = done<void>(),
    release = done<void>();
  const source = (async function* () {
    try {
      yield 'owned content';
    } finally {
      entered.resolve();
      await release.promise;
    }
  })();
  const iterator = scope.iterator(source)[Symbol.asyncIterator]();
  await iterator.next();
  scope.seal();
  const returning = iterator.return!();
  await entered.promise;
  let iterator_done = false;
  const iterator_drain = scope.settled().then(() => {
    iterator_done = true;
  });
  try {
    await next_turn();
    expect(iterator_done).toBe(false);
    await expect(scope.run(() => 'new work')).rejects.toMatchObject({ statusCode: 503 });
  } finally {
    release.resolve();
    await Promise.all([returning, iterator_drain]);
  }
  console.info(`HTTP lifetime unit proof: ${(performance.now() - began).toFixed(3)} ms`);
});
afterEach(() => vi.restoreAllMocks());
function setup() {
  const database = fixture();
  const path = fixture_path(database);
  const config = loadConfig('/missing-unit21-config', {
    PLAN_DB_PATH: path,
    PLAN_TEST_MODE: '0',
    PLAN_DISPATCH_ENABLED: 'true',
    PLAN_DISPATCHER_LOCK_PATH: path + '.dispatcher',
  });
  const context: OwnedAreaContext = {
    database,
    config,
    clock: new TestClock('2026-07-04T10:00:00+00:00'),
    changes: new ChangeSignal(),
    conversationSystem: new InMemoryConversationSystem(),
    assert_database_owned() {},
  };
  configure_conversation_start(database, { db_path: path, workspace_root: dirname(path) });
  return { context, database, path };
}
function done<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

test('one dispatcher owner admits all loops and test mode admits no factories', async () => {
  const { context, database, path } = setup();
  const owner = acquireLock(context.config.dispatcher_lock_path);
  const contender = acquireLock(context.config.dispatcher_lock_path);
  const calls: string[] = [];
  const factory: BackgroundServiceFactory = () => {
    calls.push('construct');
    return {
      start: () => {
        calls.push('start');
      },
      close: () => {
        calls.push('close');
      },
      settled: async () => {},
    };
  };
  const wakes = vi.spyOn(ManagerWakeLoop.prototype, 'start');
  try {
    expect(owner).not.toBeNull();
    expect(contender).toBeNull();
    // Each refusal is a different admission boundary, not a loop behavior matrix.
    for (const [config, owned] of [
      [{ ...context.config, test_mode: true }, true],
      [{ ...context.config, dispatch_enabled: false }, true],
      [context.config, contender !== null],
    ] as const) {
      expect(dispatcher_admitted(config, owned)).toBe(false);
      const inert = compose_background_services(
        { ...context, config },
        {
          dispatcher_owned: owned,
          enable_worker_readiness: true,
          areaLoops: { managerWakes: true },
          notificationDelivery: factory,
          stallChecker: factory,
        },
      );
      await inert.start();
      await inert.close({ deadline: performance.now() / 1000 + 1 });
    }
    expect(calls).toEqual([]);
    expect(wakes).not.toHaveBeenCalled();
    create(database);
    const admitted = compose_background_services(context, {
      dispatcher_owned: true,
      enable_worker_readiness: true,
      areaLoops: { managerWakes: true },
      notificationDelivery: factory,
      stallChecker: factory,
    });
    await admitted.start();
    await next_turn();
    await admitted.close({ deadline: performance.now() / 1000 + 1 });
    expect(calls).toEqual(['construct', 'start', 'construct', 'start', 'close', 'close']);
    expect(wakes).toHaveBeenCalledTimes(1);
    expect(database.read((sql) => sql.get('SELECT worker_step_claim FROM tickets'))).toEqual({
      worker_step_claim: 'out',
    });
  } finally {
    owner?.close();
  }
});

test('notification wakes subscribe to commits and stop before the shared drain waits', async () => {
  const { context } = setup();
  const start = vi.spyOn(NotificationLoop.prototype, 'start').mockImplementation(() => {});
  const wake = vi.spyOn(NotificationLoop.prototype, 'wake').mockImplementation(() => {});
  const stop = vi.spyOn(NotificationLoop.prototype, 'stop').mockResolvedValue();
  const first = done<void>();
  const deadlines: number[] = [];
  const order: string[] = [];
  const held: BackgroundServiceFactory = () => ({
    start() {},
    close(options) {
      order.push('held fenced');
      deadlines.push(options.deadline);
      return first.promise;
    },
    settled: () => first.promise,
  });
  const services = compose_background_services(context, {
    dispatcher_owned: true,
    notificationDelivery: production_notification_delivery,
    stallChecker: held,
  });
  await services.start();
  expect(start).toHaveBeenCalledWith(context.config.tick_seconds);
  context.changes.emit();
  expect(wake).toHaveBeenCalledTimes(1);
  const deadline = performance.now() / 1000 + 1;
  const closing = services.close({ deadline });
  expect(stop).toHaveBeenCalledWith({ deadline });
  expect(order).toEqual(['held fenced']);
  expect(deadlines).toEqual([deadline]);
  context.changes.emit();
  expect(wake).toHaveBeenCalledTimes(1);
  first.resolve();
  await closing;
});

test('update suppression and commit wakes share the composed Worker owner', async () => {
  const { context, database } = setup();
  const ticket = create(database);
  let held = true;
  const before = vi.fn(() => held);
  const services = compose_background_services(context, {
    dispatcher_owned: true,
    enable_worker_readiness: true,
    before_poll: before,
  });
  await services.start();
  expect(read(database, ticket.id).worker_step_claim).toBe('none');
  held = false;
  context.changes.emit();
  await next_turn();
  expect(read(database, ticket.id).worker_step_claim).toBe('out');
  const calls = before.mock.calls.length;
  await services.close({ deadline: performance.now() / 1000 + 1 });
  context.changes.emit();
  await next_turn();
  expect(before).toHaveBeenCalledTimes(calls);
});

test('notification deadline fences late web and native results and bounds adapter close', async () => {
  const { context, database } = setup();
  const web = done<WebPushResult>();
  const native = done<APNsResult>();
  const webEntered = done<void>();
  const nativeEntered = done<void>();
  const close = done<void>();
  vi.spyOn(notificationData, 'queue_deliveries').mockReturnValue(1);
  vi.spyOn(notificationData, 'read_web_push_identity').mockReturnValue({
    private_key: '',
    public_key: '',
  });
  const subscription = {
    subscription_id: 'one',
    endpoint: 'https://invalid',
    p256dh: '',
    auth: '',
  };
  const intent = { title: '', body: '', route: '', tag: '' };
  const edge = {
    subject_kind: 'ticket',
    subject_id: 'one',
    notification_type: 'errored',
    generation: 1,
  };
  vi.spyOn(notificationData, 'pending_deliveries').mockReturnValue([
    { edge, subscription, intent, attempts: 0 },
  ]);
  vi.spyOn(apnsData, 'pending_deliveries').mockReturnValue([
    {
      edge,
      intent,
      attempts: 0,
      created_at: now,
      device: {
        device_id: 'native',
        device_token: '',
        bundle_id: '',
        environment: 'development',
        registration_version: 1,
        updated_at: now,
      },
    },
  ]);
  const webRecord = vi.spyOn(notificationData, 'record_delivery_result');
  const nativeRecord = vi.spyOn(apnsData, 'record_result');
  const nativeSend = vi.fn(() => {
    nativeEntered.resolve();
    return native.promise;
  });
  const adapterClose = vi.fn(() => close.promise);
  const loop = new NotificationLoop({
    database,
    clock: context.clock,
    canonical_origin: 'https://unit21.invalid',
    adapter: {
      send: () => {
        webEntered.resolve();
        return web.promise;
      },
    },
    apns_adapter: { send: nativeSend, close: adapterClose },
  });
  const pass = loop.poll_once();
  try {
    await webEntered.promise;
    const stopping = loop.stop({ deadline: performance.now() / 1000 + 0.01 });
    expect(adapterClose).not.toHaveBeenCalled();
    await expect(stopping).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
    expect(adapterClose).not.toHaveBeenCalled();
  } finally {
    web.resolve({ delivered: true });
    try {
      await pass;
      await new Promise<void>((resolve) => setImmediate(resolve));
      expect(adapterClose).toHaveBeenCalledOnce();
      let actual_done = false;
      void loop.settled().then(() => {
        actual_done = true;
      });
      await new Promise<void>((resolve) => setImmediate(resolve));
      expect(actual_done).toBe(false);
    } finally {
      close.resolve();
      await loop.settled();
    }
  }
  expect(webRecord).not.toHaveBeenCalled();
  expect(nativeSend).not.toHaveBeenCalled();
  expect(await loop.poll_once()).toBe(0);
  // Reach the native await separately. A fence on the web path alone is insufficient.
  vi.mocked(notificationData.pending_deliveries).mockReturnValue([]);
  const nativeLoop = new NotificationLoop({
    database,
    clock: context.clock,
    canonical_origin: 'https://unit21.invalid',
    adapter: { send: async () => ({ delivered: true }) },
    apns_adapter: { send: nativeSend, close() {} },
  });
  const nativePass = nativeLoop.poll_once();
  try {
    await nativeEntered.promise;
    await expect(
      nativeLoop.stop({ deadline: performance.now() / 1000 + 0.01 }),
    ).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
  } finally {
    native.resolve({ delivered: true });
    await nativePass;
    await nativeLoop.settled();
  }
  expect(nativeRecord).not.toHaveBeenCalled();
});

test('startServer reconciles only its dispatcher before runtime factories and cleans a failed start', async () => {
  const { database, path } = setup();
  database.write((sql) =>
    sql.run("INSERT INTO navigation_meta VALUES(1,'unit21',10,'unit21-epoch',0)"),
  );
  // The actual lifecycle must own the database, not the borrowed fixture handle.
  database.close();
  const configuration = {
    PLAN_DB_PATH: path,
    PLAN_CONFIG_PATH: join(dirname(path), 'absent-config.yaml'),
    PLAN_DISPATCHER_LOCK_PATH: path + '.server-dispatcher',
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '0',
    PLAN_DISPATCH_ENABLED: 'true',
    PLAN_CREDENTIAL_KEY_PATH: join(dirname(path), 'key'),
  };
  for (const [key, value] of Object.entries(configuration)) vi.stubEnv(key, value);
  const configPath = join(dirname(path), 'explicit-unit21.yaml');
  writeFileSync(configPath, 'tick_seconds: 23\n');
  expect(loadConfig(undefined, { PLAN_CONFIG_PATH: configPath }).tick_seconds).toBe(23);
  expect(loadConfig(configPath, { PLAN_CONFIG_PATH: '/missing-ignored.yaml' }).tick_seconds).toBe(
    23,
  );
  const trace: string[] = [];
  const reconcile = vi.spyOn(notificationData, 'reconcile_attention').mockImplementation(() => {
    trace.push('reconcile');
  });
  try {
    const stopped: string[] = [];
    await expect(
      startServer({
        createConversationCore(context) {
          expect(trace).toEqual(['reconcile']);
          trace.push('core');
          const noProvider = () => {
            throw new Error('this startup proof never starts a provider');
          };
          return new SqliteProcessConversationSystem({
            store: context.record.store,
            message_files: new ConversationMessageFiles(path),
            backend_child_factories: { codex: noProvider, claude: noProvider, hermes: noProvider },
          });
        },
        notificationDelivery: (context) => ({
          start() {
            stopped.push('notification start');
          },
          close() {
            context.assert_database_owned();
            expect(acquireLock(configuration.PLAN_DISPATCHER_LOCK_PATH)).toBeNull();
            stopped.push('notification close');
          },
          settled: async () => {},
        }),
        stallChecker: () => ({
          start() {
            stopped.push('stall start');
            throw new Error('synthetic service refusal');
          },
          close() {
            stopped.push('stall close');
          },
          settled: async () => {},
        }),
      }),
    ).rejects.toThrow('synthetic service refusal');
    expect(stopped).toEqual([
      'notification start',
      'stall start',
      'notification close',
      'stall close',
    ]);
    expect(reconcile).toHaveBeenCalledTimes(1);
    const owner = acquireRuntimeOwnership(path);
    owner.close();
    const dispatcher = acquireLock(configuration.PLAN_DISPATCHER_LOCK_PATH);
    expect(dispatcher).not.toBeNull();
    dispatcher?.close();
  } finally {
    vi.unstubAllEnvs();
  }
});

// Gap: individual observer tests cannot prove cancellation at one shared lifecycle
// deadline while both automatic producers own active sends.
test('composed Worker and Manager sends share a deadline and leave no late database access', async () => {
  const { context, database, path } = setup();
  const ticket = create(database);
  database.write((sql) =>
    wakeData.create_worker_error_wake(sql, {
      target: { kind: 'chief', id: 'chief' },
      ticket_id: ticket.id,
      ticket_title: ticket.title,
      claim_revision: 1,
      error_reason: 'held',
      now,
    }),
  );
  let owned = true;
  let accesses = 0;
  context.assert_database_owned = () => {
    if (!owned) throw new Error('ownership ended');
  };
  context.database = {
    read(work) {
      context.assert_database_owned();
      accesses += 1;
      return database.read(work);
    },
    write(work, options) {
      context.assert_database_owned();
      accesses += 1;
      return database.write(work, options);
    },
    close() {
      throw new Error('borrowed database');
    },
  };
  configure_conversation_start(context.database, { db_path: path, workspace_root: dirname(path) });
  const provider = done<PromptDeliveryStarted>();
  const entered = done<void>();
  let sends = 0;
  const system = context.conversationSystem!;
  vi.spyOn(system, 'send').mockImplementation(() => {
    sends += 1;
    if (sends === 2) entered.resolve();
    return provider.promise;
  });
  const workerStop = vi.spyOn(WorkerStepReadinessLoop.prototype, 'stop');
  const managerStop = vi.spyOn(ManagerWakeLoop.prototype, 'stop');
  const dispatcher = acquireLock(path + '.held-dispatcher')!;
  const services = compose_background_services(context, {
    dispatcher_owned: true,
    enable_worker_readiness: true,
    areaLoops: { managerWakes: true },
  });
  try {
    await services.start();
    await entered.promise;
    const departure = read(database, ticket.id);
    const deadline = performance.now() / 1000 + 0.01;
    const closing = services.close({ deadline });
    expect(workerStop).toHaveBeenCalledWith({ deadline });
    expect(managerStop).toHaveBeenCalledWith({ deadline });
    expect(acquireLock(path + '.held-dispatcher')).toBeNull();
    await expect(closing).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
    // The parent deadline observer can run before this producer's cancellation callback.
    await expect(managerStop.mock.results[0]!.value).rejects.toBeInstanceOf(
      ShutdownDeadlineExceeded,
    );
    expect(read(database, ticket.id)).toMatchObject({
      conversation_id: departure.conversation_id,
      worker_step_claim: 'out',
      worker_step_claim_revision: departure.worker_step_claim_revision,
    });
    expect(database.read((sql) => sql.get('SELECT status FROM manager_wake_batches'))).toEqual({
      status: 'uncertain',
    });
    const before = accesses;
    expect(acquireLock(path + '.held-dispatcher')).toBeNull();
    provider.resolve(new PromptDeliveryStarted());
    await services.settled();
    context.changes.emit();
    await next_turn();
    expect(accesses).toBe(before);
    owned = false;
    dispatcher.close();
    const successor = acquireLock(path + '.held-dispatcher');
    expect(successor).not.toBeNull();
    successor?.close();
  } finally {
    provider.resolve(new PromptDeliveryStarted());
    await Promise.resolve(services.close({ deadline: performance.now() / 1000 })).catch(() => {});
    await services.settled();
    dispatcher.close();
  }
});

// Gap: a failed start does not prove that shutdown retains ownership while an
// earlier asynchronous start still has a permitted continuation.
test('close fences later starts and waits for the active start before ownership release', async () => {
  const { context, database, path } = setup();
  const entered = done<void>();
  const finish = done<void>();
  const owner = acquireLock(path + '.startup-dispatcher')!;
  let owned = true;
  let closed = false;
  const trace: string[] = [];
  const services = compose_background_services(context, {
    dispatcher_owned: true,
    notificationDelivery: () => ({
      async start() {
        trace.push('start entered');
        entered.resolve();
        await finish.promise;
        expect(owned).toBe(true);
        database.read((sql) => sql.get('SELECT 1'));
        trace.push('start settled');
      },
      close() {
        trace.push('close entered');
      },
      settled: () => finish.promise,
    }),
    stallChecker: () => {
      throw new Error('a later service must never start');
    },
  });
  const starting = services.start();
  await entered.promise;
  const closing = Promise.resolve(services.close({ deadline: performance.now() / 1000 }));
  const expired = expect(closing).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
  await next_turn();
  expect(closed).toBe(false);
  expect(acquireLock(path + '.startup-dispatcher')).toBeNull();
  expect(trace).toEqual(['start entered', 'close entered']);
  finish.resolve();
  await expired;
  await Promise.all([starting, services.settled()]);
  closed = true;
  owned = false;
  owner.close();
  expect(trace).toEqual(['start entered', 'close entered', 'start settled']);
  const successor = acquireLock(path + '.startup-dispatcher');
  expect(successor).not.toBeNull();
  successor?.close();
});

// Gap: a mocked start cannot expose synchronous commit wake reentrancy while a
// populated notification pass queues its own delivery.
test('a notification queue commit cannot reenter its active provider send', async () => {
  const setupResult = setup();
  const { context, path } = setupResult;
  const ticket = create(setupResult.database);
  setupResult.database.close();
  const database = createDatabase(path, { onChange: () => context.changes.emit() });
  context.database = database;
  database.write((sql) => {
    notificationData.initialize_notifications(sql, now);
    notificationData.register_subscription(sql, {
      endpoint: 'https://push.unit21.invalid/one',
      p256dh: 'synthetic',
      auth: 'synthetic',
      now,
    });
    record_attention(sql, 'ticket', ticket.id, 'errored', true, now);
  });
  const entered = done<void>();
  const result = done<WebPushResult>();
  const send = vi.spyOn(StandardsWebPushAdapter.prototype, 'send').mockImplementation(() => {
    entered.resolve();
    return result.promise;
  });
  const services = compose_background_services(context, {
    dispatcher_owned: true,
    notificationDelivery: production_notification_delivery,
  });
  try {
    await services.start();
    await entered.promise;
    await next_turn();
    expect(send).toHaveBeenCalledTimes(1);
    result.resolve({ delivered: true });
    await services.close({ deadline: performance.now() / 1000 + 1 });
    expect(
      database.read((sql) => sql.get('SELECT status,attempts FROM notification_deliveries')),
    ).toEqual({ status: 'delivered', attempts: 1 });
  } finally {
    result.resolve({ delivered: true });
    await Promise.resolve(services.close({ deadline: performance.now() / 1000 + 1 })).catch(
      (error: unknown) => {
        if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
      },
    );
    await services.settled();
    database.close();
  }
});
