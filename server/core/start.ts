import { writeSync } from 'node:fs';
import { assert_test_database_path } from './test_paths.ts';
import type { FastifyInstance, FastifyListenOptions } from 'fastify';
import { createAreasRuntime } from '../areas.ts';
import type { AreaLoopOptions } from '../areas.ts';
import { createApp } from '../app.ts';
import { loadConfig, SHUTDOWN_GRACE_SECONDS } from './config.ts';
import { buildClock } from './clock.ts';
import type { Clock } from './clock.ts';
import type { Config } from './config.ts';
import { ChangeSignal } from './changes/index.ts';
import { createDatabase } from './database/index.ts';
import type { Database } from './database/index.ts';
import { acquireRuntimeOwnership, acquireLock } from './locks/index.ts';

import type { Area } from './authority/register.ts';
import type { OwnedAreaContext } from './authority/register.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { ConversationViewRuntime } from '../conversation/views/routes.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { createConversationRoutes } from '../conversation/routes.ts';
import { ConversationHttpWriteScope } from '../conversation/http/writes.ts';
import { createBackendRoutes } from '../conversation/snapshot/routes.ts';
import type { BackendHttpRuntime } from '../conversation/snapshot/contracts.ts';
import { create_backend_runtime } from '../conversation/snapshot/composition.ts';
import { production_backend_providers } from '../conversation/snapshot/production.ts';
import { CredentialRequestOperations } from '../credentials/requests/service.ts';
import { createCredentialRequestRoutes } from '../credentials/requests/routes.ts';
import { makeUsageService } from '../conversation/usage/composition.ts';
import { createUsageRoutes } from '../conversation/usage/routes.ts';
import type { RecordedUsageService } from '../conversation/usage/contracts.ts';
import { run_automatic_recovery } from '../conversation/deploy_interruptions/coordinator.ts';
import { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { createVideoUploadRoutes } from '../conversation/video_uploads/routes.ts';
import { createVoiceRoutes } from '../conversation/voice_transcription/routes.ts';
import { CLEANUP_INTERVAL_SECONDS } from '../conversation/video_uploads/configuration.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import type { EnvironmentRuntime } from '../environments/contracts.ts';
import { HttpRequestScope } from './http/lifetime.ts';
import { ChildProcesses } from '../lifecycle/owned_processes.ts';
import { SHUTDOWN_HARD_SECONDS } from '../lifecycle/configuration.ts';
import { is_shutdown_business_cancellation } from '../conversation/shutdown.ts';
import { OneShotChildren } from '../lifecycle/one_shot_children.ts';
import { ShutdownCoordinator } from '../lifecycle/shutdown.ts';
import { ShutdownDeadlineExceeded } from '../lifecycle/contracts.ts';
import type {
  OwnedChildProcesses,
  OwnedOneShotChildren,
  ShutdownOwner,
  StopOptions,
} from '../lifecycle/contracts.ts';

import {
  compose_background_services,
  dispatcher_admitted,
  reconcile_background_attention,
} from '../lifecycle/background.ts';
import type {
  BackgroundCompositionContext,
  BackgroundServiceFactory,
  ComposedBackgroundService,
} from '../lifecycle/contracts.ts';
export type {
  BackgroundCompositionContext,
  BackgroundServiceFactory,
  ComposedBackgroundService,
} from '../lifecycle/contracts.ts';

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
}
export interface TestCompositionContext extends ConversationCompositionContext {
  backendServices?: BackendHttpRuntime;
  core?: SqliteProcessConversationSystem;
  conversationSystem?: ConversationSystem;
}
/** Shared process lifecycle for the normal entry point and comparison host. */
export interface ServerOptions {
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
  let video_cleanup_timer: ReturnType<typeof setInterval> | undefined;
  let video_cleanup_work: Promise<unknown> | undefined;
  const config = loadConfig();
  config.db_path = assert_test_database_path(config.db_path);
  const ownership = acquireRuntimeOwnership(config.db_path);
  let environments: EnvironmentRuntime | undefined;
  let environmentsStarting: Promise<void> | undefined;
  const areas = createAreasRuntime(() => environments?.resourceProviders ?? []);
  const conversationWrites = new ConversationHttpWriteScope();
  let dispatcher: ReturnType<typeof acquireLock> = null;
  let database: ReturnType<typeof createDatabase> | undefined;
  let background: ComposedBackgroundService | undefined;
  let publication: SignalPublicationCoordinator | undefined;
  let core: SqliteProcessConversationSystem | undefined;
  let backendServices: BackendHttpRuntime | undefined;
  let stopped_core: SqliteProcessConversationSystem | undefined;
  let stopped_backend: BackendHttpRuntime | undefined;
  let credential_requests: CredentialRequestOperations | null = null;
  let recorded_usage: RecordedUsageService | undefined;
  let stopped_usage: RecordedUsageService | undefined;
  let video_uploads: VideoUploadService | undefined;
  let app: FastifyInstance | undefined;
  let ownership_current = true;
  let signalRevoked = false;
  let recovery: Promise<void> | undefined;
  let stop_options: StopOptions | undefined;
  let setup_cleanup_failure: unknown;
  let setup_cleanup_failed = false;
  let settle_setup!: () => void;
  let fail_setup!: (error: unknown) => void;
  // This reservation precedes every factory and asynchronous setup continuation.
  const setup_actual = new Promise<void>((resolve, reject) => {
    settle_setup = resolve;
    fail_setup = reject;
  });
  void setup_actual.catch(() => {});
  const requireOwnership = () => {
    if (!ownership_current) throw new Error('the conversation runtime is shut down');
  };
  const revokeSignal = () => {
    if (signalRevoked) return;
    signalRevoked = true;
    publication?.invalidate('shutdown');
  };
  const onSignal = () => {
    void close().catch(() => {
      process.exitCode = 1;
    });
  };
  const shutdown = new ShutdownCoordinator({
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
      stages.own('startup setup', () => setup_actual);
      // Independent fences all run, even if one throws. The coordinator publishes
      // its latch/deadlines and referenced hard timer before entering this callback.
      stages.own('HTTP admission', () => request_lifetime.seal());
      stages.own('conversation write admission', () => conversationWrites.seal());
      stages.own('video admission', () => video_uploads?.seal());
      stages.own('workspace admission', () => core?.workspace_admission.seal());
      stages.own('one-shot admission', () => one_shot_children.seal());
      stages.own('HTTP listener', () => {
        app?.server.close(() => {});
      });
      stages.own('video timer', () => clearInterval(video_cleanup_timer));
      stages.own('publication', revokeSignal);
      stages.own('record admission and subscriptions', () => areas.sealRecord());
      const producer = (
        name: string,
        begin: () => void | Promise<void>,
        actual: () => Promise<void>,
      ) => {
        stages.own(name, () => {
          const flights: Promise<unknown>[] = [];
          for (const operation of [begin, actual]) {
            try {
              flights.push(Promise.resolve(operation()));
            } catch (error) {
              flights.push(Promise.reject(error));
            }
          }
          // The bounded observer is NEVER the proof of actual settlement.
          return Promise.allSettled(flights).then((results) => {
            const failures = results.flatMap((result, index) =>
              result.status === 'rejected' &&
              !(index === 0 && result.reason instanceof ShutdownDeadlineExceeded)
                ? [result.reason]
                : [],
            );
            if (failures.length) throw new AggregateError(failures, `${name} shutdown failed`);
          });
        });
      };
      producer(
        'recorded usage',
        () => {
          stopped_usage = recorded_usage;
          return recorded_usage?.stop?.(stop);
        },
        () => stopped_usage?.settled() ?? Promise.resolve(),
      );
      producer(
        'background services',
        () => background?.close(stop),
        () => background?.settled() ?? Promise.resolve(),
      );
      producer(
        'environment services',
        () => environments?.close(stop),
        () => environments?.settled() ?? Promise.resolve(),
      );
      stages.own('environment setup', () => environmentsStarting?.catch(() => {}));
      producer(
        'backend services',
        () => {
          stopped_backend = backendServices;
          return backendServices?.shutdown?.(stop);
        },
        () => stopped_backend?.settled() ?? Promise.resolve(),
      );
      // Core cancellation/detach begins before any HTTP/raw-provider wait.
      producer(
        'conversation core',
        () => {
          stopped_core = core;
          return core?.shutdown(stop);
        },
        () => stopped_core?.settled() ?? Promise.resolve(),
      );
      producer(
        'video transfers',
        () => video_uploads?.drain(stop),
        () => video_uploads?.settled() ?? Promise.resolve(),
      );
      stages.own('one-shot children', () => one_shot_children.settled());
      stages.own('HTTP server', () => app?.close());
      stages.own('HTTP work', () => request_lifetime.settled());
      stages.own('media cleanup', () => video_cleanup_work);
      stages.own('HTTP writes', () => conversationWrites.drain());
      producer(
        'credential requests',
        () => credential_requests?.finish_operations(stop),
        () => credential_requests?.settled() ?? Promise.resolve(),
      );
      stages.own('startup recovery', () =>
        recovery?.catch((error: unknown) => {
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
          database?.close();
          ownership_current = false;
        },
      },
      { name: 'dispatcher ownership', run: () => dispatcher?.close() },
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
  const close = (stop?: StopOptions): Promise<void> => shutdown.close(stop);
  const isClosing = () => shutdown.shutdown_state.kind !== 'running';
  const assert_startup_open = () => {
    if (isClosing()) throw new Error('Server closed during startup');
  };
  const abort_late_setup = async (): Promise<never> => {
    const actuals: Promise<unknown>[] = [];
    // Shutdown can run before either asynchronous factory assigns its result.
    // Retain those late resources inside setup rather than restarting close().
    if (core && core !== stopped_core) {
      stopped_core = core;
      try {
        actuals.push(
          Promise.resolve(core.shutdown(stop_options!)).catch((error: unknown) => {
            if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
          }),
        );
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
      try {
        actuals.push(core.settled());
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
    }
    if (backendServices && backendServices !== stopped_backend) {
      stopped_backend = backendServices;
      try {
        actuals.push(
          Promise.resolve(backendServices.shutdown?.(stop_options!)).catch((error: unknown) => {
            if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
          }),
        );
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
      try {
        actuals.push(backendServices.settled());
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
    }
    if (recorded_usage && recorded_usage !== stopped_usage) {
      stopped_usage = recorded_usage;
      try {
        actuals.push(
          Promise.resolve(recorded_usage.stop?.(stop_options!)).catch((error: unknown) => {
            if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
          }),
        );
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
      try {
        actuals.push(recorded_usage.settled());
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
    }
    if (environments) {
      try {
        actuals.push(
          Promise.resolve(environments.close(stop_options!)).catch((error: unknown) => {
            if (!(error instanceof ShutdownDeadlineExceeded)) throw error;
          }),
        );
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
      try {
        actuals.push(environments.settled());
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
    }
    if (app) {
      try {
        actuals.push(app.close());
      } catch (error) {
        actuals.push(Promise.reject(error));
      }
    }
    const results = await Promise.allSettled(actuals);
    const failures = results.filter((result) => result.status === 'rejected');
    if (failures.length) {
      setup_cleanup_failed = true;
      setup_cleanup_failure = new AggregateError(
        failures.map((failure) => failure.reason),
        'Late startup cleanup failed',
      );
      throw setup_cleanup_failure;
    }
    throw new Error('Server closed during startup');
  };
  const started = (address: string): StartedServer => ({
    address,
    close,
    get shutdown_state() {
      return shutdown.shutdown_state;
    },
    get shutdown_settled() {
      return shutdown.shutdown_settled;
    },
  });
  for (const signal of ['SIGTERM', 'SIGINT'] as const) process.on(signal, onSignal);
  const setup = async (): Promise<string> => {
    assert_startup_open();
    const changes = new ChangeSignal();
    const clock = buildClock(config);
    database = createDatabase(config.db_path, {
      commitSteps: areas.commitSteps,
      onChange: () => changes.emit(),
    });
    publication = new SignalPublicationCoordinator({
      database,
      require_runtime_ownership: requireOwnership,
    });
    areas.bindPublication(publication);
    areas.prepare(database);
    if (!config.test_mode && config.dispatch_enabled) {
      dispatcher = acquireLock(config.dispatcher_lock_path);
    }
    areas.initialize(database, config, clock, () => {
      if (dispatcher_admitted(config, dispatcher !== null)) {
        reconcile_background_attention({ database: database! });
      }
    });
    const context: ConversationCompositionContext = {
      database,
      config,
      clock,
      changes,
      record: areas.getRuntime(),
      publication,
      require_runtime_ownership: requireOwnership,
      revoke_signal: revokeSignal,
      one_shot_children,
      child_processes,
    };
    core = options.createConversationCore?.(context);
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    if (core)
      credential_requests = new CredentialRequestOperations({
        database,
        core,
        writes: conversationWrites,
        key_path: config.credential_key_path,
        now: () => clock.nowUnix(),
      });
    video_uploads = new VideoUploadService(database, config.db_path, {
      signal: media_lifetime.signal,
    });
    const uploads = video_uploads;
    const conversation_http = core
      ? {
          core,
          record: context.record,
          writes: conversationWrites,
        }
      : null;
    backendServices =
      options.createBackendServices?.(context) ??
      create_backend_runtime({
        database,
        config,
        now: () => clock.now(),
        providers: production_backend_providers(config.test_mode, {
          database,
          now: () => clock.now(),
          read_codex_snapshot: () => backendServices!.backend_snapshots.snapshot('codex'),
          one_shot_children,
          child_processes,
        }),
        one_shot_children,
      });
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    const conversationSystem = options.conversationSystem ?? core;
    const usage =
      options.createUsageService?.(context) ?? makeUsageService(config, { now: () => clock.now() });
    recorded_usage = usage;
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    if (core) {
      const current = core;
      areas.bindRuntimeFacts(async (conversation_id) => ({
        running: await current.is_running(conversation_id),
        permissions: [...(await current.pending_permission_ask_ids(conversation_id))],
        inputs: [...(await current.pending_user_input_request_ids(conversation_id))],
      }));
    }
    publication.bind_runtime((conversation_id) => core?.runtime_turn(conversation_id) ?? null);
    options.beforePublication?.(database);
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    publication.initialize();
    areas.attachNavigation();
    const testContext: TestCompositionContext = {
      ...context,
      core,
      conversationSystem,
      backendServices,
    };
    environments = await options.createEnvironments?.(testContext);
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    const testAreas =
      typeof options.testAreas === 'function'
        ? options.testAreas(testContext)
        : (options.testAreas ?? []);
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    const ownedContext: OwnedAreaContext = {
      database,
      config,
      clock,
      changes,
      signalAuthority: publication,
      conversationSystem,
      assert_database_owned: context.require_runtime_ownership,
    };
    background = compose_background_services(ownedContext, {
      dispatcher_owned: dispatcher !== null,
      enable_worker_readiness: options.enableWorkerReadiness,
      before_poll: environments
        ? () => {
            const environmentHold = environments!.before_worker_readiness_poll();
            const explicitHold = options.beforeWorkerReadinessPoll?.() ?? false;
            return environmentHold || explicitHold;
          }
        : options.beforeWorkerReadinessPoll,
      areaLoops: options.areaLoops,
      notificationDelivery: options.notificationDelivery,
      stallChecker: options.stallChecker,
    });
    app = await createApp(config, {
      own_app: (created) => {
        app = created;
      },
      request_lifetime,
      database,
      changes,
      areas: [
        ...areas.areas,
        { prefix: '/api', routes: createCredentialRequestRoutes(credential_requests) },
        {
          prefix: '/api/conversation/usage',
          routes: createUsageRoutes({
            conversation_runtime_available: conversationSystem !== undefined,
            recorded_usage: {
              readSummary: async (input) =>
                await conversationWrites.run(() => usage.readSummary(input)),
              refreshRates: async () => await conversationWrites.run(() => usage.refreshRates()),
              finish_operations: () => usage.finish_operations(),
              settled: () => usage.settled(),
            },
            backend_state: backendServices.backend_state,
          }),
        },
        {
          prefix: '/api/conversation/video-uploads',
          routes: createVideoUploadRoutes(video_uploads, conversation_http, media_lifetime.signal),
        },
        {
          prefix: '/api/conversation',
          routes: createVoiceRoutes({ signal: media_lifetime.signal, writes: conversationWrites }),
        },
        ...(environments?.areas ?? []),

        {
          prefix: '/api/conversation',
          routes: createBackendRoutes(backendServices, {
            available: conversationSystem !== undefined,
            writes: conversationWrites,
          }),
        },
        ...(core
          ? [
              {
                prefix: '/api/conversation',
                routes: createConversationRoutes(conversation_http!),
              },
            ]
          : []),
        ...testAreas,
      ],
      clock,
      signalAuthority: publication,
      conversationSystem,
      workspaceAdmission: conversationSystem?.workspace_admission,
    });
    if (isClosing()) return abort_late_setup();
    await app.ready();
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    areas.startRecordReplay(core?.workspace_admission);
    assert_startup_open();
    await areas.startRouteServices();
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    if (core) {
      const current = core;
      uploads.recover_and_cleanup();
      assert_startup_open();
      video_cleanup_timer = setInterval(() => {
        if (isClosing() || video_cleanup_work !== undefined) return;
        video_cleanup_work = current.workspace_admission.operation(async (admitted) => {
          if (admitted) uploads.recover_and_cleanup();
        });
        void video_cleanup_work
          .catch((error) => {
            console.error('Video upload cleanup failed', error);
          })
          .finally(() => {
            video_cleanup_work = undefined;
          });
      }, CLEANUP_INTERVAL_SECONDS * 1000);
      video_cleanup_timer.unref();
      // Retain the flight before recovery can synchronously request shutdown.
      recovery = Promise.resolve().then(() =>
        current.workspace_admission.operation(async (admitted) => {
          if (!admitted) return;
          await current.tell_agents_of_background_tasks_lost_at_restart();
          await run_automatic_recovery(current, database!);
        }),
      );

      const active_recovery = recovery;
      void active_recovery.then(
        () => {
          if (recovery === active_recovery) recovery = undefined;
        },
        (error) => {
          console.error('Conversation startup recovery failed', error);
          if (recovery === active_recovery) recovery = undefined;
        },
      );
    }
    // Publish the owned flight before setup can synchronously request shutdown.
    environmentsStarting = Promise.resolve().then(() => {
      if (!isClosing()) return environments?.start();
    });
    await environmentsStarting;
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    if (core && !config.test_mode) {
      await core.start_idle_child_janitor();
      if (isClosing()) return abort_late_setup();
    }
    assert_startup_open();
    await background.start();
    if (isClosing()) return abort_late_setup();
    assert_startup_open();
    const inherited = process.env.PLAN_SERVER_LISTENER_FD;
    if (inherited !== undefined) {
      const fd = Number(inherited);
      if (!Number.isInteger(fd) || fd < 0) throw new Error('invalid PLAN_SERVER_LISTENER_FD');
      const address = await app.listen({ fd } as FastifyListenOptions & { fd: number });
      if (isClosing()) return abort_late_setup();
      return address;
    } else {
      const address = await app.listen({ host: '127.0.0.1', port: config.port });
      if (isClosing()) return abort_late_setup();
      return address;
    }
  };
  // Publish setup ownership before any synchronous callback can request shutdown.
  const setting_up = Promise.resolve().then(setup);
  void setting_up.then(
    () => settle_setup(),
    () => (setup_cleanup_failed ? fail_setup(setup_cleanup_failure) : settle_setup()),
  );
  try {
    return started(await setting_up);
  } catch (error) {
    try {
      await close();
    } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], 'Startup and cleanup failed');
    }
    throw error;
  }
}
