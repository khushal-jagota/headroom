import { writeSync } from 'node:fs';
import type { AreaLoopOptions } from '../areas.ts';
import type { VpsExecutor } from '../executor/vps_transport.ts';
import { loadConfig, SHUTDOWN_GRACE_SECONDS } from './config.ts';
import type { Clock } from './clock.ts';
import type { Config } from './config.ts';
import type { ChangeSignal } from './changes/index.ts';
import type { Database } from './database/index.ts';
import { acquireRuntimeOwnership } from './locks/index.ts';
import type { Area } from './authority/register.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { ConversationViewRuntime } from '../conversation/views/routes.ts';
import type { OpenRunTurn } from '../conversation/record/contracts.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import type { RecordedUsageService } from '../conversation/usage/contracts.ts';
import type { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import type { EnvironmentRuntime } from '../environments/contracts.ts';
import { HttpRequestScope } from './http/lifetime.ts';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { SHUTDOWN_HARD_SECONDS } from '../lifecycle/configuration.ts';
import { is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';
import { ShutdownCoordinator } from '../lifecycle/shutdown.ts';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  ShutdownOwner,
  StopOptions,
  ShutdownCompletion,
  BackgroundCompositionContext,
  BackgroundServiceFactory,
} from '../lifecycle/contracts.ts';
export type {
  BackgroundCompositionContext,
  BackgroundServiceFactory,
  ComposedBackgroundService,
} from '../lifecycle/contracts.ts';
import { assert_test_database_path } from './test_paths.ts';
import { ServerServices } from './server_services.ts';

export interface ExecutorCompositionContext {
  readonly database: Database;
  readonly config: Config;
  readonly clock: Clock;
  readonly changes: ChangeSignal;
  readonly media_signal: AbortSignal;
  readonly one_shot_children: OwnedOneShotChildren;
  readonly child_processes: OwnedChildProcesses;
}
/** One reconciled record is shared by the core, ordinary areas, and view readers. */
export interface ConversationCompositionContext extends Omit<
  BackgroundCompositionContext,
  'assert_database_owned'
> {
  record: ConversationViewRuntime;
  publication: SignalPublicationCoordinator;
  require_runtime_ownership(): void;
  revoke_signal(): void;
  one_shot_children: OwnedOneShotChildren;
  child_processes: OwnedChildProcesses;
  executor?: VpsExecutor | null;
}
export interface TestCompositionContext extends ConversationCompositionContext {
  /** Existing original stop only. Test close hooks must never create another budget. */
  shutdown_options(): StopOptions;
  backendServices?: BackendHttpRuntime;
  core?: SqliteProcessConversationSystem;
  conversationSystem?: ConversationSystem;
}
/** Shared process lifecycle for the normal entry point and comparison host. */
export interface ServerOptions {
  /** Actual process entries supply this. Embedded servers never terminate their host. */
  onShutdownComplete?: ShutdownCompletion;
  createExecutor?: (context: ExecutorCompositionContext) => VpsExecutor | null;
  createUsageService?: (context: ConversationCompositionContext) => RecordedUsageService;
  createBackendServices?: (context: ConversationCompositionContext) => BackendHttpRuntime;
  /** Off until Unit 21 explicitly composes the production lifecycle. */
  enableWorkerReadiness?: boolean;
  /** Unit 21 supplies UpdateService.poll. True suppresses this poll. */
  beforeWorkerReadinessPoll?: () => boolean;
  /** Construction is side-effect free or cleans up before throwing. */
  createEnvironments?: (
    context: TestCompositionContext,
  ) => EnvironmentRuntime | Promise<EnvironmentRuntime>;
  testAreas?: readonly Area[] | ((context: TestCompositionContext) => readonly Area[]);
  /** Lifecycle composition opts in explicitly. Ordinary startup never polls wakes. */
  areaLoops?: AreaLoopOptions;
  /** Retained facts enter the sole reconciliation before the core adopts them. */
  readOpenRuns?: (
    context: Omit<ConversationCompositionContext, 'record'>,
  ) => ReadonlyMap<string, OpenRunTurn>;
  createConversationCore?: (
    context: ConversationCompositionContext,
  ) => SqliteProcessConversationSystem;
  /** Explicit composition switches. No factory means no task or provider adapter. */
  notificationDelivery?: BackgroundServiceFactory;
  stallChecker?: BackgroundServiceFactory;
  conversationSystem?: ConversationSystem;
  /** Test-host projection bootstrap, after reconciliation and before publication. */
  beforePublication?: (database: Database) => void;
}
export interface StartedServer extends ShutdownOwner {
  readonly address: string;
}
export async function startServer(options: ServerOptions = {}): Promise<StartedServer> {
  const media_lifetime = new AbortController();
  const request_lifetime = new HttpRequestScope();
  const child_processes = new ChildProcesses();
  const one_shot_children = new OneShotChildren(child_processes);
  const config = loadConfig();
  config.db_path = assert_test_database_path(config.db_path);
  const ownership = acquireRuntimeOwnership(config.db_path);
  let stop_options: StopOptions | undefined;
  let initial_stop: StopOptions | undefined;
  let returned = false,
    signal_requested = false,
    completion_notified = false;
  let closing: Promise<void> | undefined;
  const initialStop = (): StopOptions =>
    (initial_stop ??= {
      deadline: performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS,
    });
  const {
    promise: setup_actual,
    resolve: settle_setup,
    reject: fail_setup,
  } = Promise.withResolvers<void>();
  void setup_actual.catch(() => {});
  const services: ServerServices = new ServerServices(config, options, {
    media_lifetime,
    request_lifetime,
    child_processes,
    one_shot_children,
    isClosing: (): boolean => shutdown.shutdown_state.kind !== 'running',
    shutdown_options() {
      if (!stop_options)
        throw new Error('Server shutdown options are unavailable before stop begins');
      return stop_options;
    },
  });
  const { areas, conversationWrites } = services;
  const onSignal = () => {
    signal_requested = true;
    void close(initialStop()).catch((error) => {
      if (!options.onShutdownComplete) process.exitCode = 1;
      console.error('Server shutdown failed', error);
    });
  };
  const shutdown: ShutdownCoordinator = new ShutdownCoordinator({
    grace_seconds: SHUTDOWN_GRACE_SECONDS,
    hard_seconds: SHUTDOWN_HARD_SECONDS,
    grace_expired(stages) {
      const state = shutdown.shutdown_state;
      try {
        writeSync(
          2,
          `Shutdown grace expired; pending: ${state.pending.join(', ')}; failed: ${state.failures.map((failure) => failure.name).join(', ')}\n`,
        );
      } catch {
        /* A closed log sink must not skip cancellation. */
      }
      stages.own('HTTP connection cutoff', () => request_lifetime.force_close_connections());
      stages.own('media cancellation', () =>
        media_lifetime.abort(new Error('Media processing stopped after shutdown grace')),
      );
    },
    hard_stop(state, force_deadline) {
      // This path never awaits adapter cleanup or runs database/record finalizers.
      try {
        try {
          writeSync(
            2,
            `Shutdown hard bound; pending: ${state.pending.join(', ')}; failed: ${state.failures.map((failure) => failure.name).join(', ')}\n`,
          );
        } catch {
          /* Attempt force even when stderr is closed. */
        }
        try {
          child_processes.force_kill(force_deadline);
        } catch (error) {
          try {
            writeSync(2, `Shutdown force was not fully confirmed: ${String(error)}\n`);
          } catch {
            /* Logging cannot prevent the terminal exit. */
          }
        }
      } finally {
        process.exit(1);
      }
    },
    begin(stages, stop) {
      stop_options = stop;
      stages.own('native process admission', () => child_processes.seal());
      stages.own('startup setup', () => setup_actual);
      // Independent fences all run, even if one throws. The coordinator publishes
      // its latch/deadlines and referenced hard timer before entering this callback.
      stages.own('HTTP admission', () => request_lifetime.seal());
      stages.own('conversation write admission', () => conversationWrites.seal());
      stages.own('video admission', () => {
        services.video.value?.seal();
      });
      stages.own('preview producer admission', () => areas.sealPreviews());
      stages.own('workspace admission', () => services.core.value?.workspace_admission.seal());
      stages.own('one-shot admission', () => one_shot_children.seal());
      stages.own('HTTP listener', () => {
        services.app.value?.server.close(() => {});
      });
      stages.own('video timer', () => clearInterval(services.video_cleanup_timer));
      stages.own('publication', services.revokeSignal);
      stages.own('record admission and subscriptions', () => areas.sealRecord());
      // Stop order is explicit; every independent stop starts before a drain waits.
      for (const resource of [services.recorded_usage, services.background, services.environments])
        resource.begin(stages, stop);
      stages.own('environment setup', () => services.environmentsStarting?.catch(() => {}));
      for (const resource of [services.backend, services.executor, services.core, services.video])
        resource.begin(stages, stop);
      const http_actual = request_lifetime.settled();
      const previews = services.resources.service<typeof areas>(
        'preview services',
        (value) => value.stopPreviews(stop, http_actual),
        (value) => value.previewsSettled(),
      );
      previews.own(areas);
      previews.begin(stages, stop);
      stages.own('one-shot children', () => one_shot_children.settled());
      services.app.begin(stages, stop);
      stages.own('HTTP work', () => http_actual);
      stages.own('media cleanup', () => services.video_cleanup_work);
      stages.own('HTTP writes', () => conversationWrites.drain());
      services.credentials.begin(stages, stop);
      stages.own('startup recovery', () =>
        services.recovery?.catch((error: unknown) => {
          if (!is_shutdown_business_cancellation(error)) throw error;
        }),
      );
      // Producer cleanup can publish additional native control children. Take this
      // snapshot only AFTER every producer begins, then make a FRESH native join.
      const producer_actual = shutdown.shutdown_settled;
      stages.own('native processes and worker units', async () => {
        await producer_actual.catch(() => {}); // Individual stages retain their failures.
        await child_processes.settled();
      });
    },
    finalize: [
      { name: 'conversation record', run: () => areas.closeRecord() },
      { name: 'navigation detach', run: () => areas.detachNavigation() },
      {
        name: 'database',
        run: () => {
          services.close_database();
        },
      },
      { name: 'dispatcher ownership', run: () => services.dispatcher?.close() },
      { name: 'runtime ownership', run: () => ownership.close() },
      {
        name: 'signal handlers',
        run: () => {
          process.off('SIGTERM', onSignal);
          process.off('SIGINT', onSignal);
        },
      },
    ],
  });
  const close = (stop: StopOptions): Promise<void> => {
    if (closing) return closing;
    initial_stop ??= stop;
    const { promise, resolve, reject } = Promise.withResolvers<void>();
    closing = promise;
    void closing.catch(() => {});
    const notify = (code: 0 | 1): void => {
      if (!options.onShutdownComplete || (!returned && !signal_requested) || completion_notified)
        return;
      completion_notified = true;
      try {
        const state = shutdown.shutdown_state;
        writeSync(
          2,
          `Shutdown complete; code: ${code}; pending: ${state.pending.join(', ')}; failed: ${state.failures.map((item) => item.name).join(', ')}\n`,
        );
      } finally {
        options.onShutdownComplete(code);
      }
    };
    try {
      void shutdown.close(initial_stop).then(
        () => {
          try {
            notify(0);
            resolve();
          } catch (error) {
            reject(error);
          }
        },
        (error) => {
          try {
            notify(1);
          } catch (callbackError) {
            reject(callbackError);
            return;
          }
          reject(error);
        },
      );
    } catch (error) {
      reject(error);
    }
    return closing;
  };
  const started = (address: string): StartedServer => {
    returned = true;
    return {
      address,
      close,
      get shutdown_state() {
        return shutdown.shutdown_state;
      },
      get shutdown_settled() {
        return shutdown.shutdown_settled;
      },
    };
  };
  for (const signal of ['SIGTERM', 'SIGINT'] as const) process.on(signal, onSignal);
  // Publish setup custody before a factory can reenter shutdown.
  const setting_up = Promise.resolve().then(async () => {
    try {
      return await services.setup();
    } finally {
      try {
        await services.resources.settle_late();
        settle_setup();
      } catch (error) {
        fail_setup(error);
        throw error;
      }
    }
  });
  try {
    return started(await setting_up);
  } catch (error) {
    try {
      await close(initialStop());
    } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], 'Startup and cleanup failed');
    }
    throw error;
  }
}
