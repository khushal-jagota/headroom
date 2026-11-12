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
      unsubscribe?.();
      unsubscribe = undefined;
      return loop.stop(options);
    },
  };
};
export function production_stall_checker(
  sources: () => readonly StallSource[],
): BackgroundServiceFactory {
  return (context) =>
    new StallChecker(context.database, WAITS, sources, () => context.clock.now().getTime());
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
  const own = (factory: () => ComposedBackgroundService) => {
    let service: ComposedBackgroundService | undefined;
    services.push({
      async start() {
        service = factory();
        await service.start();
      },
      close: (stop) => service?.close(stop),
    });
  };
  if (dispatcher_admitted(context.config, options.dispatcher_owned)) {
    context.assert_database_owned();
    const factoryContext: BackgroundCompositionContext = context;
    // Construction is side-effect free. Register each owner before starting it.
    if (options.notificationDelivery) own(() => options.notificationDelivery!(factoryContext));
    if (options.stallChecker) own(() => options.stallChecker!(factoryContext));
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
      };
    });
    own(() => {
      const areas = composeAreaLoops(context, options.areaLoops);
      return { start: () => areas.start(), close: (stop) => areas.stop(stop) };
    });
  }
  return {
    start() {
      if (started || stopped) throw new Error('background services already started or stopped');
      started = true;
      starting = (async () => {
        for (const service of services) {
          if (stopped) break;
          await service.start();
        }
      })();
      return starting;
    },
    close(stop: StopOptions) {
      if (stopped) return stopped;
      // Every close fences its producer synchronously, before any drain is awaited.
      const drains = services.map((service) => {
        try {
          return Promise.resolve(service.close(stop));
        } catch (error) {
          return Promise.reject(error);
        }
      });
      // A close operation can fence a service while its asynchronous start still
      // owns permitted setup work. Retain ownership until that start settles too.
      // The start caller retains its error; it is not a second cleanup failure.
      if (starting) drains.push(starting.catch(() => {}));
      stopped = Promise.allSettled(drains).then((results) => {
        const failures = results.filter((result) => result.status === 'rejected');
        if (failures.length)
          throw new AggregateError(
            failures.map((result) => result.reason),
            'Background shutdown failed',
          );
      });
      return stopped;
    },
  };
}
