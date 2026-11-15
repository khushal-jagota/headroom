/** Shared observation tests need no filesystem, process, or provider host. */
import { afterEach, expect, test, vi } from 'vitest';
import { Deferred } from '../conversation/runtime/state.ts';
import { WorkerSupervisionRuntime } from './runtime.ts';
import { FreshProcessObservations, SharedProcessObserver } from './observation.ts';
import type { InspectionContext, WorkerProcessPrimitives } from './contracts.ts';

afterEach(() => vi.useRealTimers());
function context(snapshot: WorkerProcessPrimitives['snapshot']): InspectionContext {
  return {
    host: { host: 'host', boot: 'boot' },
    primitives: {
      owns_isolated_session_members: false,
      requires_containment: false,
      host_boot: async () => ({ host: 'host', boot: 'boot' }),
      snapshot,
      is_absent: async () => true,
      signal: async () => true,
      prepare_launch() {},
    },
  };
}

test('a failed shared pass or callback does not strand another registration, and close joins in-flight work', async () => {
  vi.useFakeTimers();
  const failure = vi.spyOn(console, 'error').mockImplementation(() => {
    throw new Error('logger failed');
  });
  const snapshot = vi
    .fn(async () => new Map())
    .mockRejectedValueOnce(new Error('inventory failed'));
  const observed = vi.fn(async () => {});
  const entered = new Deferred<void>(),
    release = new Deferred<void>();
  const observer = new SharedProcessObserver(
    async () => context(snapshot),
    () => Date.now() / 1000,
  );
  try {
    observer.register('broken', async () => {
      throw new Error('callback failed');
    });
    observer.register('healthy', observed);
    await vi.advanceTimersByTimeAsync(2000);
    expect(observed).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    expect(observed).toHaveBeenCalledTimes(2);
    expect(snapshot).toHaveBeenCalledTimes(3);
    expect(failure).toHaveBeenCalledTimes(2);
    await observer.unregister('broken');
    await observer.unregister('healthy');
    observer.register('pending', async () => {
      entered.resolve();
      await release.promise;
    });
    await vi.advanceTimersByTimeAsync(2000);
    await entered.promise;
    await observer.unregister('pending');
    let closed = false;
    const closing = observer.close().then(() => {
      closed = true;
    });
    await Promise.resolve();
    expect(closed).toBe(false);
    release.resolve();
    await closing;
    expect(closed).toBe(true);
  } finally {
    release.resolve();
    await observer.close();
    failure.mockRestore();
  }
});

test('fresh cohorts exclude callers after capture starts and a short wait does not cancel a longer member', async () => {
  const entered = new Deferred<void>(),
    release = new Deferred<void>();
  let calls = 0;
  const shared = new FreshProcessObservations(() => performance.now() / 1000);
  const selected = context(async () => {
    calls += 1;
    if (calls === 1) {
      entered.resolve();
      await release.promise;
    }
    return new Map();
  });
  try {
    const now = performance.now() / 1000;
    const longer = shared.capture(selected, now + 2);
    const short = expect(shared.capture(selected, now + 0.01)).rejects.toMatchObject({
      name: 'TimeoutError',
    });
    await entered.promise;
    const late = shared.capture(selected, now + 2);
    release.resolve();
    await longer;
    await short;
    await late;
    expect(calls).toBe(4);
  } finally {
    release.resolve();
    await shared.close();
  }
});

test('runtime closure fences external work while admitted nested cleanup retains its scope', async () => {
  const runtime = new WorkerSupervisionRuntime({
    primitives: context(async () => new Map()).primitives,
  });
  const entered = new Deferred<void>(),
    release = new Deferred<void>();
  let cleaned = false,
    external = false;
  const admitted = runtime.operation(async () => {
    entered.resolve();
    await release.promise;
    await runtime.operation(async () => {
      cleaned = true;
    });
  });
  await entered.promise;
  const closing = runtime.close();
  try {
    await expect(
      runtime.operation(async () => {
        external = true;
      }),
    ).rejects.toThrow('closed');
    release.resolve();
    await admitted;
    await closing;
    expect(cleaned).toBe(true);
    expect(external).toBe(false);
    await expect(runtime.operation(async () => {})).rejects.toThrow('closed');
  } finally {
    release.resolve();
    await admitted;
    await closing;
  }
});
