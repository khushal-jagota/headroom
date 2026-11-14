/** Production loop factories remain explicit until the production core composes them. */
import { composeAreaLoops } from '../areas.ts';
import type { AreaLoopOptions } from '../areas.ts';
import type { OwnedAreaContext } from '../core/authority/register.ts';
import { prepare_background_loops } from '../core/loops.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { NotificationLoop } from '../notifications/runtime.ts';
import { StandardsWebPushAdapter } from '../notifications/web_push.ts';
import { TokenAPNsAdapter } from '../notifications/apns.ts';
import { reconcile_attention } from '../notifications/data.ts';
import { StallChecker } from '../stalls/checker.ts';
import { WAITS } from '../stalls/registry.ts';
import type { StallSource } from '../stalls/contracts.ts';
import { observe_shutdown } from '../conversation/shutdown.ts';
import { ShutdownDeadlineExceeded } from './contracts.ts';
import type {
  BackgroundCompositionContext,
  BackgroundServiceFactory,
  ComposedBackgroundService,
  StopOptions,
} from './contracts.ts';

export function dispatcher_admitted(
  config: BackgroundCompositionContext['config'],
  owned: boolean,
) {
  return !config.test_mode && config.dispatch_enabled && owned;
}
export function reconcile_background_attention(
  context: Pick<BackgroundCompositionContext, 'database'>,
): void {
  context.database.write(reconcile_attention);
}
export const production_notification_delivery: BackgroundServiceFactory = (context) => {
  const loop = new NotificationLoop({
    database: context.database,
    clock: context.clock,
    canonical_origin: context.config.trusted_ingress_canonical_origin,
    adapter: new StandardsWebPushAdapter(),
    apns_adapter: context.config.apns ? new TokenAPNsAdapter(context.config.apns) : null,
    workspace_admission: context.conversationSystem?.workspace_admission,
  });
  let unsubscribe: (() => void) | undefined;
  return {
    start() {
      unsubscribe = context.changes.subscribe(() => loop.wake());
      loop.start(context.config.tick_seconds);
    },
    close(options) {
      let stopped!: Promise<void>;
      try {
        unsubscribe?.();
      } finally {
        unsubscribe = undefined;
        stopped = loop.stop(options);
        void stopped.catch(() => {});
      }
      return stopped;
    },
    settled: () => loop.settled(),
  };
};
export function production_stall_checker(
  sources: () => readonly StallSource[],
): BackgroundServiceFactory {
  return (context) => {
    const checker = new StallChecker(context.database, WAITS, sources, () =>
      context.clock.now().getTime(),
    );
    let actual = Promise.resolve();
    return {
      start: () => checker.start(),
      close(stop) {
        actual = checker.close();
        return observe_shutdown(
          actual,
          stop,
          new ShutdownDeadlineExceeded(['stall checker']).message,
          ['stall checker'],
        );
      },
      settled: () => actual,
    };
  };
}
export function compose_background_services(
  context: OwnedAreaContext,
  options: {
    dispatcher_owned: boolean;
    enable_worker_readiness?: boolean;
    before_poll?: () => boolean;
    areaLoops?: AreaLoopOptions;
    notificationDelivery?: BackgroundServiceFactory;
    stallChecker?: BackgroundServiceFactory;
  },
): ComposedBackgroundService {
  const services: ComposedBackgroundService[] = [];
  let stopped: Promise<void> | undefined;
  let started = false;
  let starting: Promise<void> | undefined;
  let actual_stop: Promise<void> | undefined;
  let stopping_options: StopOptions | undefined;
  const own = (factory: () => ComposedBackgroundService) => {
    let service: ComposedBackgroundService | undefined;
    let closed_service: ComposedBackgroundService | undefined;
    let late_cleanup: Promise<void> | undefined;
    services.push({
      async start() {
        service = factory();
        if (stopped) {
          // A factory can synchronously request stop before returning its owner.
          // The earlier close saw no service. Join this late cleanup explicitly.
          if (service !== closed_service) {
            closed_service = service;
            const { promise, resolve, reject } = Promise.withResolvers<void>();
            late_cleanup = promise;
            void late_cleanup.catch(() => {});
            const actuals: Promise<unknown>[] = [];
            try {
              actuals.push(
                Promise.resolve(service.close(stopping_options!)).catch((error: unknown) => {
                  if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
                }),
              );
            } catch (error) {
              actuals.push(Promise.reject(error));
            }
            try {
              actuals.push(service.settled());
            } catch (error) {
              actuals.push(Promise.reject(error));
            }
            void Promise.allSettled(actuals).then((results) => {
              const failures = results.filter((result) => result.status === 'rejected');
              if (failures.length)
                reject(
                  new AggregateError(
                    failures.map((failure) => failure.reason),
                    'Late background cleanup failed',
                  ),
                );
              else resolve();
            });
          }
          await late_cleanup;
          return;
        }
        await service.start();
      },
      close: (stop) => {
        closed_service = service;
        return service?.close(stop);
      },
      settled: () => late_cleanup ?? service?.settled() ?? Promise.resolve(),
    });
  };
  const dispatch = dispatcher_admitted(context.config, options.dispatcher_owned);
  const factoryContext: BackgroundCompositionContext = context;
  if (dispatch) {
    context.assert_database_owned();
    // Construction is side-effect free. Register each owner before starting it.
    if (options.notificationDelivery) own(() => options.notificationDelivery!(factoryContext));
  }
  // The production constructor supplies the real checker by default. Explicit test
  // factories remain allowed; dispatch and the polling lock do not gate diagnostics.
  if (options.stallChecker) own(() => options.stallChecker!(factoryContext));
  if (dispatch) {
    own(() => {
      const worker = prepare_background_loops({
        enable_worker_readiness: options.enable_worker_readiness ?? false,
        test_mode: context.config.test_mode,
        dispatch_enabled: context.config.dispatch_enabled,
        dispatcher_owned: options.dispatcher_owned,
        before_poll: options.before_poll,
        worker: context.conversationSystem
          ? {
              connect_database: () => {
                context.assert_database_owned();
                return context.database;
              },
              conversation_system: context.conversationSystem,
              worker_type_registry: configured_worker_type_registry(),
              now: () => context.clock.nowUnix(),
            }
          : null,
      });
      return {
        start: () => worker.start(context.config.tick_seconds, context.changes),
        close: (stop) => worker.stop(stop),
        settled: () => worker.settled(),
      };
    });
    own(() => {
      const areas = composeAreaLoops(context, options.areaLoops);
      return {
        start: () => areas.start(),
        close: (stop) => areas.stop(stop),
        settled: () => areas.settled(),
      };
    });
  }
  return {
    start() {
      if (started || stopped) throw new Error('background services already started or stopped');
      started = true;
      starting = Promise.resolve().then(async () => {
        for (const service of services) {
          if (stopped) break;
          await service.start();
        }
      });
      return starting;
    },
    close(stop: StopOptions) {
      if (stopped) return stopped;
      stopping_options = stop;
      const drains: Promise<unknown>[] = [];
      actual_stop = Promise.resolve().then(async () => {
        for (const service of services) {
          try {
            drains.push(service.settled());
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
            'Background shutdown failed',
          );
      });
      // Publish the fixed observer before any close callback can reenter composition.
      stopped = observe_shutdown(
        actual_stop,
        stop,
        new ShutdownDeadlineExceeded(['background services']).message,
        ['background services'],
      );
      // Every close fences its producer synchronously, before any drain is awaited.
      for (const service of services) {
        try {
          drains.push(
            Promise.resolve(service.close(stop)).catch((error: unknown) => {
              if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
            }),
          );
        } catch (error) {
          drains.push(Promise.reject(error));
        }
      }
      return stopped;
    },
    settled: () =>
      actual_stop ?? Promise.all(services.map((service) => service.settled())).then(() => {}),
  };
}
