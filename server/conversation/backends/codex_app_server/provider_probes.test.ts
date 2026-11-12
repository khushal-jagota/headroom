import { expect, test, vi } from 'vitest';
import { read_codex_rate_limits } from './usage.ts';
import { create_codex_providers } from './providers.ts';
import * as login from './login.ts';
import { Deferred } from '../../runtime/state.ts';
import type { Database } from '../../../core/database/contracts.ts';

function production_providers(database: Database = {} as Database) {
  vi.stubEnv('VITEST', '');
  vi.stubEnv('PLAN_TEST_MODE', '0');
  vi.stubEnv('PYTEST_CURRENT_TEST', undefined);
  return create_codex_providers({
    database,
    now: () => new Date(0),
    read_codex_snapshot: async () => {
      throw new Error('Unexpected snapshot lookup');
    },
  });
}

// Cancellation after rateLimits/read does not prove cancellation of a backpressured handshake write.
test('usage cancellation covers a blocked handshake and waits for owned cleanup', async () => {
  const entered = new Deferred<void>();
  const notify = new Deferred<void>();
  const cleanup = new Deferred<void>();
  const controller = new AbortController();
  const stopFailure = new Error('synthetic stop failure');
  const client = {
    async start() {},
    stop: vi.fn(async () => {
      await cleanup.promise;
      throw stopFailure;
    }),
    request: vi.fn(async () => ({})),
    request_raw: vi.fn(async () => '{"must":"not reach usage"}'),
    async notify() {
      entered.resolve();
      await notify.promise;
    },
  };
  let settled = false;
  const result = read_codex_rate_limits(
    '/synthetic/codex',
    { timeout_seconds: 30, signal: controller.signal },
    { client_factory: () => client },
  );
  void result
    .finally(() => {
      settled = true;
    })
    .catch(() => {});
  const reason = new Error('synthetic cancel during initialize notification');
  const canceled = expect(result).rejects.toBe(stopFailure);
  try {
    await entered.promise;
    controller.abort(reason);
    await Promise.resolve();
    await Promise.resolve();
    expect(client.stop).toHaveBeenCalled();
    expect(settled).toBe(false);
    cleanup.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(settled).toBe(false);
    notify.resolve();
    await canceled;
    expect(client.request_raw).not.toHaveBeenCalled();
  } finally {
    notify.resolve();
    cleanup.resolve();
    await result.catch(() => {});
  }
});

// The existing composition refusal proves construction in test mode. This proves a later context change.
test('a retained production sign-in refuses test context before its first login read', async () => {
  const read = vi.spyOn(login, 'read_stored_codex_login').mockImplementation(() => {
    throw new Error('A login read crossed the test fence');
  });
  try {
    const providers = production_providers();
    vi.stubEnv('PLAN_TEST_MODE', '1');
    await expect(providers.codex_sign_in!.start()).rejects.toThrow(
      'Production sign-in is unavailable in tests.',
    );
    expect(read).not.toHaveBeenCalled();
    await providers.codex_sign_in!.shutdown();
  } finally {
    read.mockRestore();
    vi.unstubAllEnvs();
  }
});

// A synchronous failure in one owner must not skip another owner's active child cleanup.
test('runtime shutdown joins the production catalog owner before reporting environment failure', async () => {
  const { create_backend_runtime, unavailable_backend_providers } =
    await import('../../snapshot/composition.ts');
  const { RecordingBackendProbeEnvironment } =
    await import('../../snapshot/recording_environment.ts');
  const catalog = await import('./model_catalog.ts');
  const entered = new Deferred<void>();
  const cleanup = new Deferred<void>();
  const failure = new Error('synthetic synchronous environment shutdown failure');
  let signal!: AbortSignal;
  const probe = vi
    .spyOn(catalog, 'probe_codex_model_catalog')
    .mockImplementation(async (_path, options = {}) => {
      signal = options.signal!;
      const aborted = new Promise<void>((resolve) =>
        signal.addEventListener('abort', () => resolve(), { once: true }),
      );
      entered.resolve();
      await aborted;
      await cleanup.promise;
      throw signal.reason;
    });
  let operation: Promise<unknown> | undefined;
  let stopping: Promise<void> | undefined;
  try {
    const database = {} as Database;
    const providers = production_providers(database);
    const runtime = create_backend_runtime({
      database,
      config: { test_mode: true } as import('../../../core/config.ts').Config,
      now: () => new Date(0),
      environment: Object.assign(new RecordingBackendProbeEnvironment(), {
        shutdown(): never {
          throw failure;
        },
      }),
      providers: { ...unavailable_backend_providers(), ...providers },
    });
    operation = providers.codex!('/synthetic/codex');
    void operation.catch(() => {});
    await entered.promise;
    let settled = false;
    stopping = runtime.shutdown!();
    void stopping
      .finally(() => {
        settled = true;
      })
      .catch(() => {});
    const rejected = expect(stopping).rejects.toMatchObject({ errors: [failure] });
    await Promise.resolve();
    expect(signal.aborted).toBe(true);
    expect(settled).toBe(false);
    cleanup.resolve();
    await rejected;
    await operation.catch(() => {});
  } finally {
    cleanup.resolve();
    await stopping?.catch(() => {});
    await operation?.catch(() => {});
    probe.mockRestore();
    vi.unstubAllEnvs();
  }
});
