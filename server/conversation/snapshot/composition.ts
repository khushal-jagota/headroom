/** The backend HTTP runtime over the live driver instances. Without a registry, built-in cards only. */
import type { Database } from '../../core/database/contracts.ts';
import type { Config } from '../../core/config.ts';
import type { OwnedOneShotChildren } from '../../lifecycle/contracts.ts';
import type {
  BackendCardSource,
  BackendProbeEnvironment,
  BackendHttpRuntime,
  CardDriver,
} from './contracts.ts';
import type { BackendUsageAdapter } from '../backend_usage/contracts.ts';
import type { DriverRegistry } from '../../drivers/core/registry.ts';
import type { BackendInstanceId } from '../../drivers/core/identity.ts';
import { default_instance_id_for_driver } from '../../drivers/core/identity.ts';
import type { DriverStatus } from '../../drivers/core/status.ts';
import { BUILT_IN_MANIFESTS, built_in_driver_of } from '../../drivers/built_in_manifests.ts';
import type { BuiltInDriverFacts } from '../../drivers/built_in_manifests.ts';
import { BackendSnapshotService } from './service.ts';
import { probe_card } from './probe.ts';
import { answer } from './catalogs/readers.ts';
import { identity } from './identity.ts';
import { BackendUsageService } from '../backend_usage/service.ts';
import { BackendStateStore } from '../backend_state.ts';
import { SubprocessBackendProbeEnvironment, backend_io_is_test } from './environment.ts';
import { RecordingBackendProbeEnvironment } from './recording_environment.ts';
import { observe_shutdown } from '../shutdown.ts';
import { ShutdownDeadlineExceeded } from '../../lifecycle/contracts.ts';
import {
  model_catalog_cleanup_pending,
  remember_model_catalog_failure,
} from '../backends/catalog_cleanup.ts';

/** What a missed shutdown deadline reports as still owned. */
const SHUTDOWN_PENDING = ['production backend resources'];
type UsageEntry = { readonly label: string; readonly adapter?: BackendUsageAdapter };
interface RuntimeBackends {
  readonly sources: readonly BackendCardSource[];
  readonly usage: ReadonlyMap<BackendInstanceId, UsageEntry>;
  readonly sign_ins: ReadonlyMap<string, NonNullable<ReturnType<BackendHttpRuntime['sign_in']>>>;
  readonly drivers: readonly CardDriver[];
  driver_of(backend_key: string): CardDriver | null;
}
/** The registry's live instances, in registry order. */
function registry_backends(registry: DriverRegistry): RuntimeBackends {
  const live = registry.list().flatMap((entry) => (entry.kind === 'live' ? [entry] : []));
  return {
    sources: live.map(({ driver, instance }) => ({
      instance_id: instance.instance_id,
      executable_name: driver.manifest.executable.name,
      status: instance.status,
    })),
    usage: new Map(
      live.map(({ driver, instance }): [BackendInstanceId, UsageEntry] => [
        instance.instance_id,
        instance.allowance_usage === undefined
          ? { label: driver.client.label }
          : { label: driver.client.label, adapter: instance.allowance_usage },
      ]),
    ),
    sign_ins: new Map(
      live.flatMap(({ instance }) =>
        instance.sign_in === undefined ? [] : [[instance.instance_id, instance.sign_in] as const],
      ),
    ),
    drivers: [...registry.drivers.values()],
    driver_of: (backend_key) => registry.get(backend_key)?.driver ?? null,
  };
}
/**
 * A built-in card that loads no driver runtime: no identity or catalog reader. It is only ever
 * probed in a recording environment that has no executable (see create_backend_runtime), so
 * each card is the driver's missing-binary card, as with null catalog and login sources.
 */
function manifest_only_status(driver: BuiltInDriverFacts): DriverStatus {
  return {
    version_detects_upgrade: false,
    probe: (context) =>
      probe_card(
        {
          driver_kind: driver.driver_kind,
          manifest: driver.manifest,
          models: driver.models,
          read_identity: () => identity('unknown'),
          read_catalog: async () => answer(),
        },
        context,
      ),
  };
}
/** The built-in default instances in BUILT_IN_DRIVERS order, with no sign-in or usage adapter. */
function built_in_backends(): RuntimeBackends {
  return {
    sources: BUILT_IN_MANIFESTS.map((driver) => ({
      instance_id: default_instance_id_for_driver(driver.driver_kind),
      executable_name: driver.manifest.executable.name,
      status: manifest_only_status(driver),
    })),
    usage: new Map(
      BUILT_IN_MANIFESTS.map((driver): [BackendInstanceId, UsageEntry] => [
        default_instance_id_for_driver(driver.driver_kind),
        { label: driver.client.label },
      ]),
    ),
    sign_ins: new Map(),
    drivers: BUILT_IN_MANIFESTS,
    driver_of: (backend_key) => built_in_driver_of(backend_key) ?? null,
  };
}
export function create_backend_runtime(options: {
  readonly database: Database;
  readonly config: Config;
  readonly now: () => Date;
  /** The live backends. Their dispose joins this runtime's shutdown. */
  readonly registry?: DriverRegistry;
  readonly environment?: BackendProbeEnvironment;
  readonly one_shot_children?: OwnedOneShotChildren;
}): BackendHttpRuntime {
  const { database, now, registry } = options;
  // Without a registry there is no live instance to read a card for: every card is the
  // driver's missing-binary card, read from a recording environment, as in test mode.
  const environment: BackendProbeEnvironment =
    options.environment ??
    (registry === undefined || options.config.test_mode || backend_io_is_test()
      ? new RecordingBackendProbeEnvironment()
      : new SubprocessBackendProbeEnvironment(require_children(options.one_shot_children)));
  const backends = registry === undefined ? built_in_backends() : registry_backends(registry);
  const backend_state = new BackendStateStore(database);
  const reporting = new Set<string>(
    [...backends.usage].flatMap(([key, entry]) => (entry.adapter === undefined ? [] : [key])),
  );
  let resolve_resource_join!: () => void, reject_resource_join!: (error: unknown) => void;
  const resource_join = new Promise<void>((resolve, reject) => {
    resolve_resource_join = resolve;
    reject_resource_join = reject;
  });
  let shutdown_result: Promise<void> | undefined;
  let environment_stop: Promise<PromiseSettledResult<void | undefined>[]> | undefined;
  let invoking_shutdown = false,
    finished = false;
  const cleanup_history = new Set<unknown>();
  const invoke_shutdown = (operation: () => void | Promise<void>) => {
    invoking_shutdown = true;
    try {
      return Promise.resolve(operation());
    } catch (error) {
      return Promise.reject(error);
    } finally {
      invoking_shutdown = false;
    }
  };
  return {
    shutdown(options) {
      if (!finished && !invoking_shutdown) {
        // Keep the original environment join. Each later stop can retry a native
        // close that prevents that join, without restarting the environment.
        let resolve_attempt!: () => void, reject_attempt!: (error: unknown) => void;
        // Reserve the observer before synchronous owner callbacks can reenter.
        shutdown_result = new Promise<void>((resolve, reject) => {
          resolve_attempt = resolve;
          reject_attempt = reject;
        });
        void (async () => {
          environment_stop ??= Promise.allSettled([
            invoke_shutdown(() => environment.shutdown?.()),
          ]);
          const results = await Promise.allSettled([
            invoke_shutdown(() => registry?.dispose(options)),
            environment_stop.then((results) => {
              const failures = results.filter((result) => result.status === 'rejected');
              if (failures.length)
                throw new AggregateError(failures.map((result) => result.reason));
            }),
          ]);
          const failures = results.filter((result) => result.status === 'rejected');
          for (const failure of failures)
            remember_model_catalog_failure(cleanup_history, failure.reason);
          // Both AggregateErrors below hold the failures as they came: the registry's own
          // AggregateError (one error per failed instance) and the environment's, unflattened.
          if (failures.some((failure) => model_catalog_cleanup_pending(failure.reason))) {
            throw new AggregateError(
              [...cleanup_history, ...failures.map((failure) => failure.reason)],
              'Backend cleanup remains unfinished',
            );
          }
          finished = true;
          if (cleanup_history.size)
            reject_resource_join(new AggregateError(cleanup_history, 'Backend shutdown failed'));
          else resolve_resource_join();
          await resource_join;
        })().then(resolve_attempt, reject_attempt);
      }
      return observe_shutdown(
        shutdown_result ?? resource_join,
        options,
        new ShutdownDeadlineExceeded(SHUTDOWN_PENDING).message,
        SHUTDOWN_PENDING,
      );
    },
    settled() {
      return resource_join;
    },
    database,
    backend_snapshots: new BackendSnapshotService({
      sources: backends.sources,
      keep_complete_model_list: (snapshot) => backend_state.keep_vps_model_list(snapshot),
      environment,
      now,
    }),
    backend_state,
    backend_usage: new BackendUsageService(backends.usage),
    backend_ids: backends.sources.map((source) => source.instance_id),
    sign_in: (backend_key) => backends.sign_ins.get(backend_key) ?? null,
    machine_catalog: () => null,
    drivers: backends.drivers,
    driver_of: (backend_key) => backends.driver_of(backend_key),
    reports_allowance_usage: (backend_key) => reporting.has(backend_key),
  };
}
function require_children(children: OwnedOneShotChildren | undefined): OwnedOneShotChildren {
  if (!children) throw new Error('Production backend probes require a server-owned child lifetime');
  return children;
}
