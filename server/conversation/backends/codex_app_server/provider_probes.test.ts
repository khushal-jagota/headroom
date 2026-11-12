import { expect, test, vi } from 'vitest';
import { probe_codex_model_catalog, CodexModelCatalogUnavailable } from './model_catalog.ts';
import { read_codex_rate_limits } from './usage.ts';
import { create_codex_providers } from './providers.ts';
import * as login from './login.ts';
import type { Database } from '../../../core/database/contracts.ts';

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

// The paired provider proof times out a native usage request. This proves the earlier startup boundary.
test('the model catalog deadline stops and joins a child that still starts', async () => {
  const startup = deferred();
  const client = {
    start: vi.fn(async () => startup.promise),
    stop: vi.fn(async () => {
      startup.resolve();
    }),
    request: vi.fn(async () => ({})),
    notify: vi.fn(async () => {}),
  };
  vi.useFakeTimers();
  try {
    const result = probe_codex_model_catalog('/synthetic/codex', { client_factory: () => client });
    const failure = expect(result).rejects.toMatchObject({
      constructor: CodexModelCatalogUnavailable,
      message: 'codex did not answer within 30s',
    });
    await vi.advanceTimersByTimeAsync(30_000);
    await failure;
    expect(client.stop).toHaveBeenCalledTimes(1);
    expect(client.request).not.toHaveBeenCalled();
  } finally {
    startup.resolve();
    vi.useRealTimers();
  }
});

// Cancellation after rateLimits/read does not prove cancellation of a backpressured handshake write.
test('usage cancellation covers a blocked handshake and waits for owned cleanup', async () => {
  const entered = deferred();
  const notify = deferred();
  const cleanup = deferred();
  const controller = new AbortController();
  const client = {
    async start() {},
    stop: vi.fn(async () => {
      notify.resolve();
      await cleanup.promise;
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
  const canceled = expect(result).rejects.toBe(reason);
  try {
    await entered.promise;
    controller.abort(reason);
    await Promise.resolve();
    await Promise.resolve();
    expect(client.stop).toHaveBeenCalled();
    expect(settled).toBe(false);
    cleanup.resolve();
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
    vi.stubEnv('VITEST', '');
    vi.stubEnv('PLAN_TEST_MODE', '');
    vi.stubEnv('PYTEST_CURRENT_TEST', undefined);
    const providers = create_codex_providers({ database: {} as Database, now: () => new Date(0) });
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
