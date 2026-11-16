/** Shared launch evidence. Each adapter test controls its own lifecycle race. */
import { vi } from 'vitest';
import type { StopOptions } from '../../../lifecycle/contracts.ts';
import type { WorkerSupervisionRuntime } from '../../../worker_supervision/runtime.ts';

export function worker_supervision_fixture(pid = 4321) {
  const order: string[] = [];
  const identity = {
    pid,
    birth: 'birth',
    boot: 'boot',
    unit: null,
    supervision_record: '/synthetic/supervision.json',
  };
  const result = { complete: true, reason: 'gone', scope: 'ticket' };
  const supervisor = {
    path: identity.supervision_record,
    environment: { PLAN_WORKER_PROCESS_ROOT: '/synthetic/records' },
    argv: (argv: readonly string[]) => ['/synthetic/bootstrap', ...argv],
    spawned: vi.fn(async () => {
      order.push('spawned');
    }),
    started: vi.fn(async () => {
      order.push('started');
    }),
    identity: vi.fn(async () => identity),
    cancel_unspawned: vi.fn(async () => {
      order.push('cancel');
    }),
    stop: vi.fn(async (native_stop?: () => Promise<void>, _options?: StopOptions) => {
      order.push('stop');
      if (result.complete) await native_stop?.();
      return { ...result };
    }),
    shutdown_settled: Promise.resolve(),
  };
  const scratch = { environment: { TMPDIR: '/synthetic/scratch' }, close: vi.fn(async () => {}) };
  return {
    identity,
    order,
    result,
    supervisor,
    scratch,
    runtime: { clock: () => 10 } as unknown as WorkerSupervisionRuntime,
  };
}
