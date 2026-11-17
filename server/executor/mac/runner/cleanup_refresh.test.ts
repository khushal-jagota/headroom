/** Gap: concurrent refreshes must not publish an older snapshot after a newer one.
 * Reuse native fixture options and inject only the snapshot, with no stores or children started.
 */
import { expect, test, vi } from 'vitest';
import { Deferred } from '../../../core/deferred.ts';
import type {
  ExecutorCleanupSnapshot,
  CleanupReconciliationDependencies,
} from '../cleanup_contracts.ts';
import type { AsyncCommandJournal } from '../storage/journal_contracts.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import { ExecutorPaths } from '../configuration.ts';
import { native_fixture_options, unexpected_workspaces } from '../testing/runner_fixture.ts';
import { MacRunner } from './index.ts';

const mocks = vi.hoisted(() => ({ snapshot: vi.fn() }));
vi.mock('../cleanup_reconciliation.ts', async (original) => ({
  ...(await original<typeof import('../cleanup_reconciliation.ts')>()),
  snapshot_cleanup: mocks.snapshot,
}));

test('refresh capture and publication share the same owner', async () => {
  const entered = new Deferred<void>();
  const release = new Deferred<ExecutorCleanupSnapshot>();
  mocks.snapshot
    .mockReset()
    .mockImplementationOnce(async () => {
      entered.resolve();
      return release.promise;
    })
    .mockResolvedValue({ uncertainties: [], sources: [], notice: 'fresh' });
  const unexpected = async (): Promise<never> => {
    throw new Error('No operational work belongs to refresh');
  };
  const runner = new MacRunner({
    ...unexpected_workspaces,
    incarnation: 'test',
    // Refresh never reads these owners; no persistence or native work starts.
    journal: {} as AsyncCommandJournal,
    outbox: {} as OutboxStore,
    cleanup: {} as CleanupReconciliationDependencies,
    now_ms: () => 0,
    new_update_id: () => {
      throw new Error('No update');
    },
    live: null,
    inputs: { download_inputs: unexpected },
    native: native_fixture_options({
      paths: new ExecutorPaths('/synthetic/state'),
      workspace_home: '/synthetic',
      native_skill_home: () => ({ path: '/synthetic/native', skills_directory: 'skills' }),
      snapshot_directory: '/synthetic/skills',
      on_failure: (error) => {
        throw error;
      },
      factory: () => () => {
        throw new Error('No native child');
      },
    }),
  });
  const first = runner.refresh_worker_cleanup();
  await entered.promise;
  const second = runner.refresh_worker_cleanup();
  await Promise.resolve();
  expect(mocks.snapshot).toHaveBeenCalledTimes(1);
  release.resolve({ uncertainties: [], sources: [], notice: 'old' });
  await Promise.all([first, second]);
  expect(runner.legacy_cleanup_notice).toBe('fresh');
  await runner.stop({ deadline: performance.now() / 1000 + 2 });
  await runner.settled();
});
