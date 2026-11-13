/** The real HTTP components and the retained Hermes catalog share one shutdown join. */
import { ConversationBackendKey } from '../conversation/contracts.ts';
import { create_backend_runtime } from '../conversation/snapshot/composition.ts';
import { production_backend_providers } from '../conversation/snapshot/production.ts';
import { backend_io_is_test } from '../conversation/snapshot/environment.ts';
import { HermesModelCatalogOwner } from '../conversation/backends/hermes_acp/catalog.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import type { ProductionBindingContext } from './production_bindings.ts';
import type { ProductionBackendLaunches } from './production_launches.ts';
import { ShutdownDeadlineExceeded } from './contracts.ts';
import { observe_shutdown } from './deadline.ts';

function own_hermes_catalog(
  base: BackendHttpRuntime,
  owner: HermesModelCatalogOwner,
): BackendHttpRuntime {
  let resolve!: () => void, reject!: (error: unknown) => void;
  const actual = new Promise<void>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  let closing = false;
  return {
    database: base.database,
    backend_snapshots: base.backend_snapshots,
    backend_state: base.backend_state,
    backend_usage: base.backend_usage,
    codex_sign_in: base.codex_sign_in,
    machine_catalog: () => base.machine_catalog(),
    shutdown(options) {
      if (!closing) {
        closing = true;
        const invoke = (operation: () => unknown): Promise<unknown> => {
          try {
            return Promise.resolve(operation());
          } catch (error) {
            return Promise.reject(error);
          }
        };
        // Reserve actual above, then begin both cleanups before any callback can await.
        const base_stop = invoke(() => base.shutdown?.(options));
        const hermes_stop = invoke(() => owner.shutdown());
        void Promise.allSettled([
          invoke(() => base.settled()),
          hermes_stop,
          base_stop.catch((error) => {
            if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
          }),
        ]).then((results) => {
          const failures = results.filter((value) => value.status === 'rejected');
          if (failures.length)
            reject(
              new AggregateError(
                failures.map((value) => value.reason),
                'Production backend shutdown failed',
              ),
            );
          else resolve();
        });
      }
      return observe_shutdown(actual, options, 'production backend resources');
    },
    settled: () => actual,
  };
}

export function create_production_backend_http(
  context: Pick<
    ProductionBindingContext,
    'database' | 'config' | 'clock' | 'child_processes' | 'one_shot_children'
  >,
  launches: Pick<ProductionBackendLaunches, 'hermes_catalog'>,
): BackendHttpRuntime {
  let runtime!: BackendHttpRuntime;
  const now = () => context.clock.now();
  const providers = production_backend_providers(context.config.test_mode, {
    database: context.database,
    now,
    child_processes: context.child_processes,
    one_shot_children: context.one_shot_children,
    read_codex_snapshot: () => runtime.backend_snapshots.snapshot(ConversationBackendKey.codex),
  });
  const owner =
    context.config.test_mode || backend_io_is_test()
      ? null
      : new HermesModelCatalogOwner(launches.hermes_catalog, {
          child_processes: context.child_processes,
        });
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
