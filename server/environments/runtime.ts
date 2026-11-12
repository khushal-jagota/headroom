/** Per-server composition. External release and executor operations remain explicit. */
import { dirname } from 'node:path';
import type { Config } from '../core/config.ts';
import { HOST } from '../core/config.ts';
import type { TestCompositionContext } from '../core/start.ts';
import type { EnvironmentRuntime } from './contracts.ts';
import { expand_path, resolved_path } from './paths.ts';
import type {
  InstallerOperations,
  MacUpdateOperations,
  ReleaseStaging,
  UpdateProvider,
} from './updates/contracts.ts';
import { FileUpdateAttemptStore, attempt_path } from './updates/attempts.ts';
import { UpdateService, installed_update_root } from './updates/service.ts';
import { create_update_routes } from './updates/routes.ts';
import type { UpdateMacReads } from './updates/routes.ts';
import { DeploymentResources } from './updates/resource_versions.ts';
import { deployment_status } from './updates/logic/experience.ts';
import { DEPLOYMENT_SUCCESS_DISPLAY_SECONDS } from './updates/configuration.ts';
import { load_metrics_config, store_path } from './vps_metrics/configuration.ts';
import { MetricsRuntime } from './vps_metrics/runtime.ts';
import type { CollectorFactory } from './vps_metrics/sampler/contracts.ts';
import { create_metrics_routes } from './vps_metrics/routes.ts';
import { create_vps_status_routes } from './vps_status/routes.ts';
import type { VpsStatusSnapshot } from './vps_status/contracts.ts';
import { layout_for } from './ticket_scratch/collect.ts';
import { clean_finished_ticket_scratch } from './ticket_scratch/apply.ts';
import { create_ticket_scratch_runtime } from './ticket_scratch/runtime.ts';

export interface EnvironmentOperations {
  app_root: string;
  service_unit: string | null;
  /** Called only for an installed release that runs in its own service unit. */
  provider(): UpdateProvider | null;
  staging: ReleaseStaging;
  installer: InstallerOperations;
  mac: MacUpdateOperations;
  mac_reads: UpdateMacReads;
  collector: CollectorFactory | null;
  collect_status(config: Config): Promise<VpsStatusSnapshot>;
}
export async function createEnvironmentRuntime(
  context: TestCompositionContext,
  operations: EnvironmentOperations,
): Promise<EnvironmentRuntime> {
  const { config, changes } = context;
  const database = await resolved_path(config.db_path);
  const data_directory = dirname(database);
  const current_root = dirname(data_directory);
  const installed = await installed_update_root(
    config.app_sha,
    operations.app_root,
    database,
    operations.service_unit,
  );
  const scratch_layout = config.test_mode
    ? null
    : await layout_for(config.ticket_scratch_root, config.ticket_scratch_database, database);
  const scratch =
    scratch_layout === null
      ? null
      : create_ticket_scratch_runtime({
          layout: scratch_layout,
          db_path: database,
          test_mode: config.test_mode,
          now_unix: () => context.clock.nowUnix(),
          assert_database_owned: context.require_runtime_ownership,
          clean_finished_ticket_scratch,
        });
  const metrics = new MetricsRuntime({
    path: await store_path(database),
    config: load_metrics_config(),
    collector: config.test_mode ? null : operations.collector,
  });
  const attempts = new FileUpdateAttemptStore(attempt_path(data_directory), () => changes.emit());
  const updates = new UpdateService({
    provider: installed === null ? null : operations.provider(),
    attempts,
    runtime: {
      database: context.database,
      conversation: () => context.conversationSystem ?? null,
      assert_database_owned: context.require_runtime_ownership,
    },
    changes,
    current_root,
    database,
    backup_directory: expand_path(config.backup_dir),
    current_app_sha: config.app_sha,
    health_url: `http://${HOST}:${config.port}/api/health`,
    service_unit: operations.service_unit ?? '',
    staging: operations.staging,
    installer: operations.installer,
    mac: operations.mac,
  });
  const deployment = () =>
    deployment_status(attempts.read(), config.app_sha, {
      success_seconds: DEPLOYMENT_SUCCESS_DISPLAY_SECONDS,
      now: new Date().toISOString(),
    });
  let starting: Promise<void> | undefined;
  let closing: Promise<void> | undefined;
  return {
    areas: [
      { prefix: '/api', routes: create_update_routes(updates, operations.mac_reads) },
      { prefix: '/api', routes: create_metrics_routes(metrics) },
      { prefix: '/api', routes: create_vps_status_routes(operations.collect_status) },
      {
        prefix: '/api',
        routes: async (app) => {
          app.get('/deployment-status', async () => deployment());
        },
      },
    ],
    resourceProviders: [new DeploymentResources(deployment)],
    before_worker_readiness_poll: () => updates.poll(),
    start() {
      if (starting) return starting;
      if (closing) return Promise.reject(new Error('Environment runtime is shut down'));
      starting = Promise.resolve().then(async () => {
        if (closing) return;
        attempts.import_legacy_request(data_directory, config.app_sha);
        if (closing) return;
        await metrics.start();
        if (!closing) updates.start();
        if (!closing) scratch?.start();
      });
      return starting;
    },
    close(options) {
      if (closing) return closing;
      // Fence every producer now, including when setup still owns a continuation.
      const begin = async (operation: () => void | Promise<void>) => {
        await operation();
      };
      const drains = [
        begin(() => metrics.stop(options)),
        begin(() => updates.stop(options)),
        begin(() => scratch?.close(options)),
        starting?.catch(() => {}),
      ];
      closing = Promise.allSettled(drains).then((results) => {
        const failures = results.filter((result) => result.status === 'rejected');
        if (failures.length)
          throw new AggregateError(
            failures.map((result) => result.reason),
            'Environment shutdown failed',
          );
      });
      return closing;
    },
  };
}
