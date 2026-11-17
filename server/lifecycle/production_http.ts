/** The real HTTP components and the retained Hermes catalog share one shutdown join. */
import type { BackendInstanceId } from '../drivers/core/identity.ts';
import { create_backend_runtime } from '../conversation/snapshot/composition.ts';
import { production_backend_providers } from '../conversation/snapshot/production.ts';
import { backend_io_is_test } from '../conversation/snapshot/environment.ts';
import {
  HermesModelCatalogOwner,
  hermes_model_catalog_launch,
} from '../drivers/hermes/runtime/catalog.ts';
import { hermes_launch_options } from '../drivers/hermes/runtime/instance.ts';
import {
  model_catalog_cleanup_pending,
  remember_model_catalog_failure,
} from '../conversation/backends/catalog_cleanup.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import type { ProductionBindingContext } from './production_bindings.ts';
import { ShutdownDeadlineExceeded } from './contracts.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';

function own_hermes_catalog(
  base: BackendHttpRuntime,
  owner: HermesModelCatalogOwner,
): BackendHttpRuntime {
  const { promise: actual, resolve, reject } = Promise.withResolvers<void>();
  let attempt: Promise<void> | undefined;
  let invoking = false,
    finished = false;
  const history = new Set<unknown>();
  const invoke = (operation: () => unknown): Promise<unknown> => {
    invoking = true;
    try {
      return Promise.resolve(operation());
    } catch (error) {
      return Promise.reject(error);
    } finally {
      invoking = false;
    }
  };
  return {
    database: base.database,
    backend_snapshots: base.backend_snapshots,
    backend_state: base.backend_state,
    backend_usage: base.backend_usage,
    codex_sign_in: base.codex_sign_in,
    machine_catalog: (machine_id) => base.machine_catalog(machine_id),
    shutdown(options) {
      if (!finished && !invoking) {
        const {
          promise,
          resolve: resolve_attempt,
          reject: reject_attempt,
        } = Promise.withResolvers<void>();
        // Reserve this observer before either owner can synchronously reenter shutdown.
        attempt = promise;
        const base_stop = invoke(() => base.shutdown?.(options));
        const hermes_stop = invoke(() => owner.shutdown());
        void (async () => {
          const results = await Promise.allSettled([
            hermes_stop,
            base_stop.catch((error) => {
              if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
            }),
          ]);
          const failures = results.filter((value) => value.status === 'rejected');
          for (const failure of failures) remember_model_catalog_failure(history, failure.reason);
          if (failures.some((failure) => model_catalog_cleanup_pending(failure.reason)))
            throw new AggregateError(
              failures.map((value) => value.reason),
              'Production backend cleanup remains unfinished',
            );
          // Failed native cleanup leaves the original raw barrier pending until a retry.
          const base_join = await Promise.allSettled([invoke(() => base.settled())]);
          for (const result of base_join)
            if (result.status === 'rejected')
              remember_model_catalog_failure(history, result.reason);
          finished = true;
          if (history.size)
            reject(new AggregateError([...history], 'Production backend shutdown failed'));
          else resolve();
          await actual;
        })().then(resolve_attempt, reject_attempt);
      }
      return observe_shutdown(
        attempt ?? actual,
        options,
        new ShutdownDeadlineExceeded(['production backend resources']).message,
        ['production backend resources'],
      );
    },
    settled: () => actual,
  };
}

export function create_production_backend_http(
  context: Pick<
    ProductionBindingContext,
    'database' | 'config' | 'clock' | 'child_processes' | 'one_shot_children' | 'driver_host'
  >,
): BackendHttpRuntime {
  let runtime!: BackendHttpRuntime;
  const now = () => context.clock.now();
  const providers = production_backend_providers(context.config.test_mode, {
    database: context.database,
    now,
    child_processes: context.child_processes,
    one_shot_children: context.one_shot_children,
    read_codex_snapshot: () => runtime.backend_snapshots.snapshot('codex' as BackendInstanceId),
  });
  const owner =
    context.config.test_mode || backend_io_is_test()
      ? null
      : new HermesModelCatalogOwner(
          hermes_model_catalog_launch(hermes_launch_options(context.driver_host)),
          {
            child_processes: context.child_processes,
          },
        );
  const base = create_backend_runtime({
    database: context.database,
    config: context.config,
    now,
    one_shot_children: context.one_shot_children,
    providers:
      owner === null
        ? providers
        : {
            ...providers,
            hermes: async (options) => {
              if (context.config.test_mode || backend_io_is_test())
                throw new Error(
                  'Production backend I/O is unavailable in tests. Inject recording adapters.',
                );
              return owner.source(options);
            },
          },
  });
  runtime = owner === null ? base : own_hermes_catalog(base, owner);
  return runtime;
}
