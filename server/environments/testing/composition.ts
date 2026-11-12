/** Existing comparison host: retained metrics, local update files, no external effects. */
import { dirname } from 'node:path';
import type { TestCompositionContext } from '../../core/start.ts';
import { createEnvironmentRuntime } from '../runtime.ts';
import { collect_vps_status } from '../vps_status/collect.ts';

const unavailable = (): never => {
  throw new Error('External environment operations are unavailable in the comparison host');
};
export function create_test_environment_runtime(context: TestCompositionContext) {
  return createEnvironmentRuntime(context, {
    app_root: dirname(context.config.db_path),
    service_unit: null,
    provider: () => null,
    collector: null,
    staging: { stage: async () => unavailable() },
    installer: {
      require_exact_schema_frontier: unavailable,
      schema_change_pending: unavailable,
      create_backup: async () => unavailable(),
      prepare: unavailable,
      launch: async () => unavailable(),
    },
    mac: {
      prepare: async () => unavailable(),
      phase: () => null,
      reported_at: () => null,
      configured: () => false,
    },
    mac_reads: {
      health_payload: () => null,
      status: async () => null,
    },
    collect_status: (config) =>
      collect_vps_status(config, {
        application_root: dirname(config.db_path),
        dependencies: {
          now: () => context.clock.isoformat(),
          statvfs: unavailable,
          process_lines: unavailable,
          git_worktree_output: unavailable,
          live_reference_paths: async () => [],
          platform_name: () => (process.platform === 'darwin' ? 'Darwin' : 'Linux'),
        },
      }),
  });
}
