import { assert_test_database_path } from './test_paths.ts';
import type { FastifyInstance, FastifyListenOptions } from 'fastify';
import {
  areas,
  commitSteps,
  initializeAreas,
  prepareAreas,
  closeAreas,
  getConversationRuntime,
  bindConversationRuntimeFacts,
} from '../areas.ts';
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
import type { AreaLoopOptions } from '../areas.ts';
import type { OwnedAreaContext } from './authority/register.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { ConversationViewRuntime } from '../conversation/views/routes.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { createConversationRoutes } from '../conversation/routes.ts';
import { createIdlePublicationAuthority } from '../conversation/publication/authority.ts';
import type { SignalPublicationAuthority } from '../conversation/publication/authority.ts';

import {
  compose_background_services,
  dispatcher_admitted,
  reconcile_background_attention,
} from '../lifecycle/background.ts';
import type {
  BackgroundCompositionContext,
  BackgroundServiceFactory,
  ComposedBackgroundService,
  RunningServer,
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
  require_runtime_ownership(): void;
}
export interface TestCompositionContext extends ConversationCompositionContext {
  core?: SqliteProcessConversationSystem;
  conversationSystem?: ConversationSystem;
}
/** Shared process lifecycle for the normal entry point and comparison host. */
export interface ServerOptions {
  /** Off until Unit 21 explicitly composes the production lifecycle. */
  enableWorkerReadiness?: boolean;
  /** Unit 21 supplies UpdateService.poll. True suppresses this poll. */
  beforeWorkerReadinessPoll?: () => boolean;
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
  createSignalAuthority?: (database: Database) => SignalPublicationAuthority;
}
export async function startServer(options: ServerOptions = {}): Promise<RunningServer> {
  const config = loadConfig();
  config.db_path = assert_test_database_path(config.db_path);
  const ownership = acquireRuntimeOwnership(config.db_path);
  let dispatcher: ReturnType<typeof acquireLock> = null;
  let database: ReturnType<typeof createDatabase> | undefined;
  let background: ComposedBackgroundService | undefined;
  let signalAuthority: SignalPublicationAuthority | undefined;
  let core: SqliteProcessConversationSystem | undefined;
  let app: FastifyInstance | undefined;
  let ownership_current = true;
  let closing: Promise<void> | undefined;
  const signalHandlers = new Map<NodeJS.Signals, () => void>();
  const close = (): Promise<void> => {
    if (closing) return closing;
    signalAuthority?.invalidate('shutdown');
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
      await attempt(() => background?.close({ deadline }));
      // Core and record settlement have no deadline contract. Retain ownership
      // through their final writes; the total provider bound remains open.
      await attempt(() => core?.shutdown());
      await attempt(() => closeAreas());
      await attempt(() => app?.close());
      if (failures.length)
        throw new AggregateError(failures, 'Server shutdown failed; ownership retained');
      ownership_current = false;
      database?.close();
      dispatcher?.close();
      ownership.close();
      for (const [signal, handler] of signalHandlers) process.removeListener(signal, handler);
    })();
    return closing;
  };
  try {
    const changes = new ChangeSignal();
    const clock = buildClock(config);
    database = createDatabase(config.db_path, { commitSteps, onChange: () => changes.emit() });
    prepareAreas(database);
    signalAuthority = (options.createSignalAuthority ?? createIdlePublicationAuthority)(database);
    if (!config.test_mode && config.dispatch_enabled) {
      dispatcher = acquireLock(config.dispatcher_lock_path);
    }
    initializeAreas(database, signalAuthority, config, clock, () => {
      if (dispatcher_admitted(config, dispatcher !== null)) {
        reconcile_background_attention({ database: database! });
      }
    });
    const context: ConversationCompositionContext = {
      database,
      config,
      clock,
      changes,
      record: getConversationRuntime(),
      require_runtime_ownership() {
        if (!ownership_current) throw new Error('the conversation runtime is shut down');
      },
    };
    core = options.createConversationCore?.(context);
    const conversationSystem = options.conversationSystem ?? core;
    if (core) {
      const current = core;
      bindConversationRuntimeFacts(async (conversation_id) => ({
        running: await current.is_running(conversation_id),
        permissions: [...(await current.pending_permission_ask_ids(conversation_id))],
        inputs: [...(await current.pending_user_input_request_ids(conversation_id))],
      }));
    }
    const testContext: TestCompositionContext = { ...context, core, conversationSystem };
    const testAreas =
      typeof options.testAreas === 'function'
        ? options.testAreas(testContext)
        : (options.testAreas ?? []);
    const ownedContext: OwnedAreaContext = {
      database,
      config,
      clock,
      changes,
      signalAuthority,
      conversationSystem,
      assert_database_owned: context.require_runtime_ownership,
    };
    background = compose_background_services(ownedContext, {
      dispatcher_owned: dispatcher !== null,
      enable_worker_readiness: options.enableWorkerReadiness,
      before_poll: options.beforeWorkerReadinessPoll,
      areaLoops: options.areaLoops,
      notificationDelivery: options.notificationDelivery,
      stallChecker: options.stallChecker,
    });
    app = await createApp(config, {
      database,
      changes,
      areas: [
        ...areas,
        ...(core
          ? [
              {
                prefix: '/api/conversation',
                routes: createConversationRoutes({ core, record: context.record }),
              },
            ]
          : []),
        ...testAreas,
      ],
      clock,
      signalAuthority,
      conversationSystem,
      workspaceAdmission: conversationSystem?.workspace_admission,
    });
    for (const signal of ['SIGTERM', 'SIGINT'] as const) {
      const handler = () => {
        void close().catch((error) => {
          console.error(error);
          process.exitCode = 1;
        });
      };
      signalHandlers.set(signal, handler);
      process.once(signal, handler);
    }
    await background.start();
    if (closing) {
      await closing;
      return { close };
    }
    const inherited = process.env.PLAN_SERVER_LISTENER_FD;
    if (inherited !== undefined) {
      const fd = Number(inherited);
      if (!Number.isInteger(fd) || fd < 0) throw new Error('invalid PLAN_SERVER_LISTENER_FD');
      await app.listen({ fd } as FastifyListenOptions & { fd: number });
    } else {
      await app.listen({ host: '127.0.0.1', port: config.port });
    }
    return { close };
  } catch (error) {
    try {
      await close();
    } catch (cleanupError) {
      throw new AggregateError([error, cleanupError], 'Startup and cleanup failed');
    }
    throw error;
  }
}
