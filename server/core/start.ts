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
import { run_automatic_recovery } from '../conversation/deploy_interruptions/coordinator.ts';
import { SignalPublicationCoordinator } from '../conversation/publication/coordinator.ts';
import type { EnvironmentRuntime } from '../environments/contracts.ts';

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
}
export interface TestCompositionContext extends ConversationCompositionContext {
  backendServices?: BackendHttpRuntime;
  core?: SqliteProcessConversationSystem;
  conversationSystem?: ConversationSystem;
}
/** Shared process lifecycle for the normal entry point and comparison host. */
export interface ServerOptions {
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
export interface StartedServer {
  readonly address: string;
  close(): Promise<void>;
}
export async function startServer(options: ServerOptions = {}): Promise<StartedServer> {
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
  let app: FastifyInstance | undefined;
  let ownership_current = true;
  let signalRevoked = false;
  let closing: Promise<void> | undefined;
  let recovery: Promise<void> | undefined;
  const requireOwnership = () => {
    if (!ownership_current) throw new Error('the conversation runtime is shut down');
  };
  const revokeSignal = () => {
    if (signalRevoked) return;
    signalRevoked = true;
    publication?.invalidate('shutdown');
  };
  const onSignal = () => {
    void close().catch((error) => {
      console.error(error);
      process.exitCode = 1;
    });
  };
  const close = (): Promise<void> => {
    if (closing) return closing;
    conversationWrites.seal();
    revokeSignal();
    areas.closeSubscriptions();
    const deadline = performance.now() / 1000 + SHUTDOWN_GRACE_SECONDS;
    const failures: unknown[] = [];
    const attempt = async (operation: () => unknown) => {
      try {
        await operation();
      } catch (error) {
        failures.push(error);
      }
    };
    closing = (async () => {
      // Fence every producer before awaiting either drain. Ownership also covers
      // environment setup that was already pending when shutdown began.
      await Promise.all([
        attempt(() => background?.close({ deadline })),
        attempt(() => environments?.close({ deadline })),
        attempt(() => environmentsStarting?.catch(() => {})),
        attempt(() =>
          backendServices?.shutdown
            ? backendServices.shutdown()
            : backendServices?.codex_sign_in?.shutdown(),
        ),
      ]);
      await attempt(() => conversationWrites.drain());
      // Loop cancellation shares one deadline. Core and record settlement have
      // no deadline contract, so retain ownership through their final writes.
      await attempt(() => core?.shutdown());
      await attempt(() => recovery);
      await attempt(() => areas.closeRecord());
      await attempt(() => app?.close());
      await attempt(() => areas.detachNavigation());
      if (failures.length)
        throw new AggregateError(failures, 'Server shutdown failed; ownership retained');
      ownership_current = false;
      database?.close();
      dispatcher?.close();
      ownership.close();
      process.off('SIGTERM', onSignal);
      process.off('SIGINT', onSignal);
    })();
    return closing;
  };
  try {
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
    };
    core = options.createConversationCore?.(context);
    backendServices =
      options.createBackendServices?.(context) ??
      create_backend_runtime({
        database,
        config,
        now: () => clock.now(),
        providers: production_backend_providers(config.test_mode, {
          database,
          now: () => clock.now(),
        }),
      });
    const conversationSystem = options.conversationSystem ?? core;
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
    publication.initialize();
    areas.attachNavigation();
    const testContext: TestCompositionContext = {
      ...context,
      core,
      conversationSystem,
      backendServices,
    };
    environments = await options.createEnvironments?.(testContext);
    const testAreas =
      typeof options.testAreas === 'function'
        ? options.testAreas(testContext)
        : (options.testAreas ?? []);
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
      database,
      changes,
      areas: [
        ...areas.areas,
        ...(environments?.areas ?? []),
        { prefix: '/api/conversation', routes: createBackendRoutes(backendServices) },
        ...(core
          ? [
              {
                prefix: '/api/conversation',
                routes: createConversationRoutes({
                  core,
                  record: context.record,
                  writes: conversationWrites,
                }),
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
    for (const signal of ['SIGTERM', 'SIGINT'] as const) {
      process.once(signal, onSignal);
    }
    if (core) {
      const current = core;
      // Retain the flight before recovery can synchronously request shutdown.
      recovery = Promise.resolve().then(() =>
        current.workspace_admission.operation(async (admitted) => {
          if (!admitted) return;
          await current.tell_agents_of_background_tasks_lost_at_restart();
          await run_automatic_recovery(current, database!);
        }),
      );
      void recovery.catch((error) => console.error('Conversation startup recovery failed', error));
    }
    // Publish the owned flight before setup can synchronously request shutdown.
    environmentsStarting = Promise.resolve().then(() => {
      if (!closing) return environments?.start();
    });
    await environmentsStarting;
    if (closing) {
      await closing;
      throw new Error('Server closed during startup');
    }
    await background.start();
    if (closing) {
      await closing;
      throw new Error('Server closed during startup');
    }
    const inherited = process.env.PLAN_SERVER_LISTENER_FD;
    if (inherited !== undefined) {
      const fd = Number(inherited);
      if (!Number.isInteger(fd) || fd < 0) throw new Error('invalid PLAN_SERVER_LISTENER_FD');
      const address = await app.listen({ fd } as FastifyListenOptions & { fd: number });
      return { address, close };
    } else {
      const address = await app.listen({ host: '127.0.0.1', port: config.port });
      return { address, close };
    }
  } catch (error) {
    try {
      await close();
    } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], 'Startup and cleanup failed');
    }
    throw error;
  }
}
