/** Queued process checks must retain their place before a caller's next check. */
import { expect, test, vi } from 'vitest';
import type { Database } from '../core/database/contracts.ts';
import { PreviewService } from '../previews/service.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { StopOptions } from '../lifecycle/contracts.ts';

test('an older queued inspection precedes a new microtask chain', async () => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'performance'] });
  const database: Database = {
    read() {
      throw new Error('The admission test must not read a database');
    },
    write() {
      throw new Error('The admission test must not write a database');
    },
    close() {
      throw new Error('The admission test must not close a database');
    },
  };
  const service = new PreviewService(database, 'https://comparison.invalid');
  const run = (
    service as unknown as {
      run<T>(operation: () => Promise<T>): Promise<T>;
    }
  ).run.bind(service);
  const started: string[] = [];
  let release_first!: () => void;
  const first = new Promise<void>((resolve) => {
    release_first = resolve;
  });
  const operation = async (label: string, delay: number): Promise<string> => {
    started.push(label);
    if (label === 'new-0') release_first();
    await new Promise((resolve) => setTimeout(resolve, delay));
    return label;
  };
  let stop: StopOptions | undefined;
  let release_held = () => {};
  let held: Promise<unknown> = Promise.resolve();
  try {
    const chain = (async () => {
      for (let number = 0; number < 8; number++) await run(() => operation('new-' + number, 150));
      return 'new chain complete';
    })();
    await first;
    const old = run(() => operation('old', 0));
    const results = Promise.allSettled([chain, old]);
    await vi.advanceTimersByTimeAsync(1250);
    expect(await results).toEqual([
      { status: 'fulfilled', value: 'new chain complete' },
      { status: 'fulfilled', value: 'old' },
    ]);
    expect(started).toEqual([
      'new-0',
      'old',
      'new-1',
      'new-2',
      'new-3',
      'new-4',
      'new-5',
      'new-6',
      'new-7',
    ]);
    let enter!: () => void;
    const entered = new Promise<void>((resolve) => {
      enter = resolve;
    });
    const inspection = new Promise<void>((resolve) => {
      release_held = resolve;
    });
    held = run(async () => {
      enter();
      await inspection;
    });
    await entered;
    const closing = service.close((stop = { deadline: performance.now() / 1000 + 0.01 }));
    const bounded = expect(closing).rejects.toBeInstanceOf(ShutdownDeadlineExceeded);
    let settled = false;
    const actual = service.settled().then(() => {
      settled = true;
    });
    await vi.advanceTimersByTimeAsync(11);
    await bounded;
    expect(settled).toBe(false);
    await expect(run(async () => 'late inspection')).rejects.toThrow();
    release_held();
    await Promise.all([held, actual]);
    expect(settled).toBe(true);
  } finally {
    try {
      release_held();
      await held;
      await service
        .close((stop ??= { deadline: performance.now() / 1000 + 1 }))
        .catch((error: unknown) => {
          if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
        });
      await service.settled();
    } finally {
      vi.useRealTimers();
    }
  }
});
