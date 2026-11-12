/** Queued process checks must retain their place before a caller's next check. */
import { expect, test, vi } from 'vitest';
import type { Database } from '../core/database/contracts.ts';
import { PreviewService } from '../previews/service.ts';

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
  } finally {
    await service.close();
    vi.useRealTimers();
  }
});
