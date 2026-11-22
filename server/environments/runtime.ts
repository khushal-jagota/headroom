/** Per-server composition. External release and executor operations remain explicit. */
import { dirname, join } from 'node:path';
import { executor_platform } from '../machines/executor_credentials.ts';
import { UpdateBarrier } from './update_barrier.ts';
import { notify_update_failure } from './updates/failure_notice.ts';
import type { UpdateAttempt } from './updates/contracts.ts';
import type { Config } from '../core/config.ts';
import { HOST } from '../core/config.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type { TestCompositionContext } from '../core/start.ts';
import type { EnvironmentRuntime } from './contracts.ts';
import { expand_path, resolved_path } from './paths.ts';
import type { InstallerOperations, ReleaseStaging, UpdateProvider } from './updates/contracts.ts';
import { FileUpdateAttemptStore, attempt_path } from './updates/attempts.ts';
import { UpdateService, installed_update_root } from './updates/service.ts';
import { create_update_routes } from './updates/routes.ts';
import type {
  MacUpdateComposition,
  MacUpdateCompositionContext,
  MacUpdateServiceContract,
} from './updates/mac/contracts.ts';
import { DeploymentResources } from './updates/resource_versions.ts';
import { deployment_status } from './updates/logic/experience.ts';
import { DEPLOYMENT_SUCCESS_DISPLAY_SECONDS } from './updates/configuration.ts';
import { load_metrics_config, store_path } from './vps_metrics/configuration.ts';
import { MetricsRuntime } from './vps_metrics/runtime.ts';
import type { CollectorFactory } from './vps_metrics/sampler/contracts.ts';
import { create_metrics_routes } from './vps_metrics/routes.ts';
import { create_vps_status_routes } from './vps_status/routes.ts';
import type { VpsStatusSnapshot } from './vps_status/contracts.ts';
import type { StatusOptions } from './vps_status/contracts.ts';
import { create_vps_status_dependencies } from './vps_status/probes.ts';
import { create_server_move } from '../server_move/composition.ts';
import type { ServerPackageSource } from '../server_move/packages/cache.ts';

export interface EnvironmentOperations {
  app_root: string;
  service_unit: string | null;
  /** Called only for an installed release that runs in its own service unit. */
  provider(): UpdateProvider | null;
  staging: ReleaseStaging;
  installer: InstallerOperations;
  create_mac(context: MacUpdateCompositionContext): MacUpdateComposition;
  collector: CollectorFactory | null;
  collect_status(config: Config, options: StatusOptions): Promise<VpsStatusSnapshot>;
  /** The release host for another platform's server package, when one is configured. */
  server_packages?(): ServerPackageSource | null;
}
/** The concrete composition exposes its existing Mac service for executor routes. */
export interface ComposedEnvironmentRuntime extends EnvironmentRuntime {
  readonly mac_updates: MacUpdateServiceContract;
}
export async function createEnvironmentRuntime(
  context: TestCompositionContext,
  operations: EnvironmentOperations,
): Promise<ComposedEnvironmentRuntime> {
  const { config, changes } = context;
  const database = await resolved_path(config.db_path);
  const data_directory = dirname(database);
  const update_barrier =
    context.update_barrier ?? new UpdateBarrier(join(data_directory, 'update-barrier.json'));
  const current_root = dirname(data_directory);
  const installed = await installed_update_root(
    config.app_sha,
    operations.app_root,
    database,
    operations.service_unit,
  );
  const metrics = new MetricsRuntime({
    path: await store_path(database),
    config: load_metrics_config(),
    collector: config.test_mode ? null : operations.collector,
  });
  const attempts = new FileUpdateAttemptStore(attempt_path(data_directory), () => changes.emit());
  const provider = installed === null ? null : operations.provider();
  const move = create_server_move({
    config,
    database: context.database,
    data_directory,
    database_path: database,
    update_barrier,
    update_running: () => {
      const attempt = attempts.read();
      return (
        (attempt !== null && attempt.state !== 'done' && attempt.state !== 'failed') ||
        update_barrier.frozen ||
        update_barrier.installer_active(attempt?.attempt_id ?? '')
      );
    },
    conversation: () => context.conversationSystem ?? null,
    package_source: config.test_mode ? null : (operations.server_packages?.() ?? null),
    emit_change: () => changes.emit(),
  });
  let updates: UpdateService;
  const mac = operations.create_mac({
    root: join(data_directory, 'mac-updates'),
    platform_for: (machine_id) =>
      context.database.read((sql) => executor_platform(sql, machine_id)),
    linux_registered: () =>
      context.database.read((sql) =>
        Boolean(
          sql.get("SELECT 1 FROM machine_executor_credentials WHERE platform='linux' LIMIT 1"),
        ),
      ),
    attempts,
    provider,
    current_app_sha: config.app_sha,
    child_processes: context.child_processes,
    signal: context.one_shot_children.signal,
    ready_release: () => updates.ready_release(),
    emit_change: () => changes.emit(),
  });
  updates = new UpdateService({
    provider,
    attempts,
    runtime: {
      database: context.database,
      conversation: () => context.conversationSystem ?? null,
      assert_database_owned: context.require_runtime_ownership,
    },
    changes,
    current_root,
    database,
    backup_directory: await resolved_path(config.backup_dir),
    current_app_sha: config.app_sha,
    health_url: `http://${HOST}:${config.port}/api/health`,
    service_unit: operations.service_unit ?? '',
    staging: operations.staging,
    installer: operations.installer,
    mac: mac.operations,
    update_barrier,
    install_refusal: () => move.coordinator.update_refusal(),
    // A test-mode server must never instantiate a provider transport.
    ...(config.test_mode
      ? {}
      : {
          notify_failure: (attempt: UpdateAttempt) =>
            void notify_update_failure(context.database, config, attempt).catch((error: unknown) =>
              console.error('The update failure push did not send', error),
            ),
        }),
  });
  const deployment = () =>
    deployment_status(attempts.read(), config.app_sha, {
      success_seconds: DEPLOYMENT_SUCCESS_DISPLAY_SECONDS,
      now: new Date().toISOString(),
    });
  let starting: Promise<void> | undefined;
  let closing: Promise<void> | undefined;
  let actual_stop: Promise<void> | undefined;
  return {
    mac_updates: mac.service,
    update_barrier,
    recover_update_barrier() {
      if (!update_barrier.frozen || update_barrier.move_hold !== null) return;
      const generation = update_barrier.generation;
      const attempt_id = attempts.read()?.attempt_id ?? '';
      if (updates.owns_preparation(attempt_id)) return;
      update_barrier.installer_guard(attempt_id, (active) => {
        if (active) {
          updates.installer_observed(attempt_id);
          return;
        }
        if (updates.owns_preparation(attempt_id)) return;
        const released = updates.recover_orphaned_attempt(attempt_id, generation);
        if (released !== null) update_barrier.remove(released);
      });
    },
    update_attempt_id: () => attempts.read()?.attempt_id ?? '',
    reopen_update_work(attempt_id) {
      updates.reopen_trial_work(attempt_id);
    },
    executor_routes: [move.executor_routes],
    server_move_notice: (machine_id) => move.coordinator.notice(machine_id),
    areas: [
      { prefix: '/api', routes: create_update_routes(updates, mac.reads) },
      { prefix: '/api', routes: move.owner_routes },
      { prefix: '/api', routes: create_metrics_routes(metrics) },
      {
        prefix: '/api',
        routes: create_vps_status_routes((status_config) =>
          operations.collect_status(status_config, {
            application_root: operations.app_root,
            dependencies: create_vps_status_dependencies(context.one_shot_children),
          }),
        ),
      },
      {
        prefix: '/api',
        routes: async (app) => {
          app.get('/deployment-status', async () => deployment());
        },
      },
    ],
    resourceProviders: [new DeploymentResources(deployment)],
    before_worker_readiness_poll: () => {
      const updating = updates.poll();
      return move.coordinator.holds_work() || updating;
    },
    start() {
      if (starting) return starting;
      if (closing) return Promise.reject(new Error('Environment runtime is shut down'));
      starting = Promise.resolve().then(async () => {
        if (closing) return;
        attempts.import_legacy_request(data_directory, config.app_sha);
        if (closing) return;
        await metrics.start();
        if (!closing) updates.start();
      });
      return starting;
    },
    close(options) {
      if (closing) return closing;
      const drains: Promise<unknown>[] = [];
      actual_stop = Promise.resolve().then(async () => {
        const actuals = [
          () => metrics.settled(),
          () => updates.settled(),
          () => mac.service.settled(),
          () => move.coordinator.settled(),
        ];
        for (const actual of actuals) {
          try {
            drains.push(Promise.resolve(actual()));
          } catch (error) {
            drains.push(Promise.reject(error));
          }
        }
        if (starting) drains.push(starting.catch(() => {}));
        const results = await Promise.allSettled(drains);
        const failures = results.filter((result) => result.status === 'rejected');
        if (failures.length)
          throw new AggregateError(
            failures.map((result) => result.reason),
            'Environment shutdown failed',
          );
      });
      closing = observe_shutdown(
        actual_stop,
        options,
        'Shutdown deadline expired; ownership retained: environment services',
        ['environment services'],
      );
      // Fence every producer now, including when setup still owns a continuation.
      for (const stop of [
        () => metrics.stop(options),
        () => updates.stop(options),
        () => move.coordinator.stop(),
      ]) {
        try {
          drains.push(
            Promise.resolve(stop()).catch((error: unknown) => {
              if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
            }),
          );
        } catch (error) {
          drains.push(Promise.reject(error));
        }
      }
      return closing;
    },
    settled: () =>
      actual_stop ??
      Promise.all([
        metrics.settled(),
        updates.settled(),
        mac.service.settled(),
        move.coordinator.settled(),
        starting?.catch(() => {}),
      ]).then(() => {}),
  };
}
