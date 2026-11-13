/** The loop tests do not cover environment setup or its separate drain and providers. */
import { afterEach, expect, test, vi } from 'vitest';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { fixture, fixture_path } from './ticket_fixture.ts';
import { startServer } from '../core/start.ts';
import { acquireRuntimeOwnership } from '../core/locks/index.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import { create_test_environment_runtime } from '../environments/testing/composition.ts';
import * as scratchLayout from '../environments/ticket_scratch/collect.ts';
import * as scratchRuntime from '../environments/ticket_scratch/runtime.ts';
import { MetricsRuntime } from '../environments/vps_metrics/runtime.ts';
import { UpdateService } from '../environments/updates/service.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';
import * as preview_routes from '../previews/routes.ts';
import { PreviewService } from '../previews/service.ts';
import { PreviewError, type PreviewRecord } from '../previews/contracts.ts';

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});
function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}
function setup() {
  const database = fixture();
  const path = fixture_path(database);
  const root = dirname(path);
  database.write((tx) => tx.run("INSERT INTO navigation_meta VALUES(1,'env',10,'env-epoch',0)"));
  database.close();
  mkdirSync(path + '.scratch');
  for (const [key, suffix] of Object.entries({
    HOME: 'home',
    CODEX_HOME: 'codex',
    CLAUDE_CONFIG_DIR: 'claude',
    HERMES_HOME: 'hermes',
    PLAN_HERMES_HOME: 'plan-hermes',
  })) {
    const home = path + '.' + suffix;
    mkdirSync(home);
    vi.stubEnv(key, home);
  }
  for (const [key, value] of Object.entries({
    PLAN_DB_PATH: path,
    PLAN_CONFIG_PATH: path + '.absent-config',
    PLAN_APP_ROOT: root,
    PLAN_WORKSPACE_ROOT: root,
    PLAN_LOGS_DIR: path + '.logs',
    PLAN_BACKUP_DIR: path + '.backups',
    PLAN_DISPATCHER_LOCK_PATH: path + '.dispatcher',
    PLAN_SERVER_CONTROL_SOCKET: path + '.control',
    PLAN_CREDENTIAL_KEY_PATH: path + '.key',
    PLAN_TICKET_SCRATCH_ROOT: path + '.scratch',
    PLAN_TICKET_SCRATCH_DATABASE: path,
    PLAN_PORT: '0',
    PLAN_TEST_MODE: '0',
    PLAN_DISPATCH_ENABLED: 'true',
  }))
    vi.stubEnv(key, value);
  return path;
}

test('environment providers and poll hooks share the server, and both producer groups fence before either drain', async () => {
  const path = setup();
  const backgroundDrain = deferred();
  const environmentDrain = deferred();
  const scratchDrain = deferred();
  const trace: string[] = [];
  const deadlines: number[] = [];
  const environmentPoll = vi.fn(() => true);
  const explicitPoll = vi.fn(() => false);
  let environmentStop: StopOptions | undefined;
  const scratchClose = vi.fn((options: StopOptions) => {
    trace.push('scratch fenced');
    deadlines.push(options.deadline);
    return scratchDrain.promise;
  });
  const scratchStart = vi.fn();
  const scratchFactory = vi.spyOn(scratchRuntime, 'create_ticket_scratch_runtime').mockReturnValue({
    start: scratchStart,
    close: scratchClose,
    settled: () => scratchDrain.promise,
  });
  const layout = vi.spyOn(scratchLayout, 'layout_for');
  const metricsStop = vi.spyOn(MetricsRuntime.prototype, 'stop');
  const updatesStop = vi.spyOn(UpdateService.prototype, 'stop');
  const completed = vi.fn();
  const http_entered = deferred(),
    before_verified = deferred();
  const release_before = deferred(),
    release_after = deferred();
  let prepared!: preview_routes.PreparedPreviewRoutes, preview!: PreviewService;
  const create_preview = preview_routes.createPreviewRoutes;
  vi.spyOn(preview_routes, 'createPreviewRoutes').mockImplementation(() => {
    prepared = create_preview();
    return prepared;
  });
  const record: PreviewRecord = {
    ticket_id: 'synthetic-preview',
    generation: 'synthetic-generation',
    worktree: dirname(path),
    public_origin: 'https://comparison.invalid',
    server_epoch: 'synthetic-epoch',
    created_at: 0,
    state: 'active',
    ended_reason: null,
    process: {
      boot_id: 'synthetic',
      pid: 1,
      start_ticks: 1,
      listener_inode: 1,
      port: 1,
      worktree: dirname(path),
    },
  };
  const start_preview = PreviewService.prototype.start;
  vi.spyOn(PreviewService.prototype, 'start').mockImplementation(async function (
    this: PreviewService,
  ) {
    preview = this;
    // Replace only inspection. Public check/before/after retain real run/admission.
    vi.spyOn(this as unknown as { inspect(): Promise<PreviewRecord> }, 'inspect').mockResolvedValue(
      record,
    );
    vi.spyOn(this.inspector, 'connected').mockResolvedValue(undefined);
    await start_preview.call(this);
  });
  const server = await startServer({
    onShutdownComplete: completed,
    testAreas: (context) => [
      {
        prefix: '/preview-grace-proof',
        routes: async (app) => {
          app.get('/held', async () => {
            http_entered.resolve();
            await release_before.promise;
            await preview.before_connection(record);
            before_verified.resolve();
            await release_after.promise;
            await preview.after_connection(record, 1);
            return context.database.read((sql) => sql.get('SELECT 1 AS alive'));
          });
        },
      },
    ],
    conversationSystem: new InMemoryConversationSystem(),
    enableWorkerReadiness: true,
    beforeWorkerReadinessPoll: explicitPoll,
    notificationDelivery: () => ({
      start() {},
      close({ deadline }) {
        trace.push('background fenced');
        deadlines.push(deadline);
        return backgroundDrain.promise;
      },
      settled: () => backgroundDrain.promise,
    }),
    async createEnvironments(context) {
      const environment = await create_test_environment_runtime(context);
      return {
        ...environment,
        resourceProviders: [
          ...environment.resourceProviders,
          {
            capture(_sql, _request, key) {
              if (key !== '/api/environment-composition-proof') return null;
              const body = { source: 'this server' };
              return { revision: 'same capture', read: () => body };
            },
          },
        ],
        before_worker_readiness_poll() {
          environment.before_worker_readiness_poll();
          return environmentPoll();
        },
        start() {
          trace.push('environment started');
          return environment.start();
        },
        close(options) {
          context.require_runtime_ownership();
          environmentStop = options;
          trace.push('environment fenced');
          deadlines.push(options.deadline);
          return Promise.all([environment.close(options), environmentDrain.promise]).then(
            () => undefined,
          );
        },
      };
    },
  });
  let stop: StopOptions | undefined;
  let verification: Promise<{ status: number; body: unknown }> | undefined;
  try {
    expect(environmentPoll).toHaveBeenCalled();
    expect(explicitPoll).toHaveBeenCalled();
    expect(layout).toHaveBeenCalledWith(path + '.scratch', path, path);
    expect(scratchFactory).toHaveBeenCalledTimes(1);
    expect(scratchStart).toHaveBeenCalledTimes(1);
    const response = await fetch(
      server.address +
        '/api/resource-body?key=' +
        encodeURIComponent('/api/environment-composition-proof'),
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      revision: 'same capture',
      body: { source: 'this server' },
    });
    const updates = await fetch(server.address + '/api/updates');
    expect(updates.status).toBe(200);
    expect(await updates.json()).toMatchObject({ state: 'up_to_date', activity: 'idle' });
    const metrics = await fetch(server.address + '/api/vps-metrics/latest');
    expect(metrics.status).toBe(200);
    expect(await metrics.json()).toMatchObject({ sequence: null, sample: null });
    const deployment = await fetch(
      server.address + '/api/resource-body?key=' + encodeURIComponent('/api/deployment-status'),
    );
    expect(deployment.status).toBe(200);
    const captured = await deployment.json();
    expect(captured.body.state).toBe('idle');
    expect(JSON.parse(captured.revision)).toEqual(captured.body);
    verification = fetch(server.address + '/preview-grace-proof/held').then(async (response) => ({
      status: response.status,
      body: await response.json(),
    }));
    await Promise.race([
      http_entered.promise,
      verification.then(() => {
        throw new Error('Preview proof request returned before its owned handler entered');
      }),
    ]);
    const closing = server.close((stop = { deadline: performance.now() / 1000 + 1 }));
    let previews_settled = false;
    const preview_actual = prepared.settled().then(() => {
      previews_settled = true;
    });
    expect(trace).toEqual([
      'environment started',
      'background fenced',
      'environment fenced',
      'scratch fenced',
    ]);
    expect(new Set(deadlines).size).toBe(1);
    expect(metricsStop.mock.calls[0]![0]).toBe(environmentStop);
    expect(updatesStop.mock.calls[0]![0]).toBe(environmentStop);
    expect(scratchClose.mock.calls[0]![0]).toBe(environmentStop);
    expect(completed).not.toHaveBeenCalled();
    environmentDrain.resolve();
    backgroundDrain.resolve();
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    expect(completed).not.toHaveBeenCalled();
    scratchDrain.resolve();
    await new Promise<void>((resolve) => setImmediate(resolve));
    expect(previews_settled).toBe(false);
    release_before.resolve();
    await Promise.race([
      before_verified.promise,
      verification.then(() => {
        throw new Error('Admitted preview verification returned before its after-connection gate');
      }),
    ]);
    expect(previews_settled).toBe(false);
    release_after.resolve();
    expect(await verification).toEqual({ status: 200, body: { alive: 1 } });
    await preview_actual;
    await closing;
    await expect(preview.check(record.ticket_id, record.generation)).rejects.toBeInstanceOf(
      PreviewError,
    );
    expect(completed).toHaveBeenCalledExactlyOnceWith(0);
  } finally {
    backgroundDrain.resolve();
    environmentDrain.resolve();
    scratchDrain.resolve();
    release_before.resolve();
    release_after.resolve();
    await verification?.catch(() => {});
    await server.close((stop ??= { deadline: performance.now() / 1000 + 1 }));
  }
  acquireRuntimeOwnership(path).close();
});

test('shutdown during environment setup retains ownership and prevents later producers and listener startup', async () => {
  const path = setup();
  const entered = deferred();
  const finish = deferred();
  const closed = deferred();
  let close_count = 0;
  const priorHandlers = process.listeners('SIGTERM');
  const background = vi.fn(() => {
    throw new Error('a producer must not start after shutdown');
  });
  const scratchStart = vi.fn();
  const scratchFactory = vi.spyOn(scratchRuntime, 'create_ticket_scratch_runtime').mockReturnValue({
    start: scratchStart,
    close() {},
    settled: async () => {},
  });
  const layout = vi.spyOn(scratchLayout, 'layout_for');
  const updatesStart = vi.spyOn(UpdateService.prototype, 'start');
  const metricsStart = vi.spyOn(MetricsRuntime.prototype, 'start');
  const starting = startServer({
    notificationDelivery: background,
    async createEnvironments(context) {
      const stop = process.listeners('SIGTERM').find((handler) => !priorHandlers.includes(handler));
      expect(stop).toBeDefined();
      // Stop while the asynchronous constructor has not returned any runtime yet.
      stop!('SIGTERM');
      entered.resolve();
      await finish.promise;
      context.require_runtime_ownership();
      context.database.read((sql) => sql.get('SELECT 1'));
      const inert = await create_test_environment_runtime({
        ...context,
        config: { ...context.config, test_mode: true },
      });
      try {
        expect(layout).not.toHaveBeenCalled();
        expect(scratchFactory).not.toHaveBeenCalled();
      } finally {
        await inert.close({ deadline: performance.now() / 1000 + 1 });
        await inert.settled();
      }
      const environment = await create_test_environment_runtime(context);
      return {
        ...environment,
        close(options) {
          close_count++;
          const flight = environment.close(options);
          closed.resolve();
          return flight;
        },
      };
    },
  });
  // Observe early setup errors too, rather than waiting for a barrier never reached.
  const outcome = starting.then(
    (server) => ({ server }),
    (error: unknown) => ({ error }),
  );
  try {
    await Promise.race([
      entered.promise,
      outcome.then((result) => {
        if ('error' in result) throw result.error;
        throw new Error('the listener started before environment setup settled');
      }),
    ]);
    expect(() => acquireRuntimeOwnership(path)).toThrow('ConversationRuntimeAlreadyOwned');
    expect(background).not.toHaveBeenCalled();
  } finally {
    finish.resolve();
    const result = await outcome;
    await closed.promise;
    if ('server' in result) await result.server.close({ deadline: performance.now() / 1000 + 1 });
    expect(result).toMatchObject({ error: { message: 'Server closed during startup' } });
  }
  expect(background).not.toHaveBeenCalled();
  expect(updatesStart).not.toHaveBeenCalled();
  expect(metricsStart).not.toHaveBeenCalled();
  expect(scratchFactory).toHaveBeenCalledTimes(1);
  expect(scratchStart).not.toHaveBeenCalled();
  expect(close_count).toBe(1);
  acquireRuntimeOwnership(path).close();
});
