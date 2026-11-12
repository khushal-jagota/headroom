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
import { loadConfig } from './config.ts';
import { buildClock } from './clock.ts';
import { prepare_background_loops, type BackgroundLoops } from './loops.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import type { Clock } from './clock.ts';
import type { Config } from './config.ts';
import { ChangeSignal } from './changes/index.ts';
import { createDatabase } from './database/index.ts';
import type { Database } from './database/index.ts';
import { acquireRuntimeOwnership, acquireLock } from './locks/index.ts';

import type { Area } from './authority/register.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import type { ConversationViewRuntime } from '../conversation/views/routes.ts';
import type { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { createConversationRoutes } from '../conversation/routes.ts';
import { createIdlePublicationAuthority } from '../conversation/publication/authority.ts';
import type { SignalPublicationAuthority } from '../conversation/publication/authority.ts';

/** Unit 21 supplies these factories explicitly. Undefined means off. */
export interface ComposedBackgroundService {
  start(): void | Promise<void>;
  close(): void | Promise<void>;
}
export interface BackgroundCompositionContext {
  database: Database;
  config: Config;
  clock: Clock;
  changes: ChangeSignal;
}
export type BackgroundServiceFactory = (
  context: BackgroundCompositionContext,
) => ComposedBackgroundService;

/** One reconciled record is shared by the core, ordinary areas, and view readers. */
export interface ConversationCompositionContext extends BackgroundCompositionContext {
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
  createConversationCore?: (
    context: ConversationCompositionContext,
  ) => SqliteProcessConversationSystem;
  /** Explicit composition switches. No factory means no task or provider adapter. */
  notificationDelivery?: BackgroundServiceFactory;
  stallChecker?: BackgroundServiceFactory;
  conversationSystem?: ConversationSystem;
  createSignalAuthority?: (database: Database) => SignalPublicationAuthority;
}
export async function startServer(options: ServerOptions = {}): Promise<void> {
  const config = loadConfig();
  const ownership = acquireRuntimeOwnership(config.db_path);
  let dispatcher: ReturnType<typeof acquireLock> = null;
  let database: ReturnType<typeof createDatabase> | undefined;
  let loops: BackgroundLoops | undefined;
  let signalAuthority: SignalPublicationAuthority | undefined;
  let core: SqliteProcessConversationSystem | undefined;
  let app: FastifyInstance | undefined;
  let ownership_current = true;
  const background: ComposedBackgroundService[] = [];
  const closeBackground = async () => {
    let failure: unknown;
    for (const service of background.splice(0).reverse()) {
      try {
        await service.close();
      } catch (error) {
        failure ??= error;
      }
    }
    if (failure) throw failure;
  };
  try {
    const changes = new ChangeSignal();
    const clock = buildClock(config);
    database = createDatabase(config.db_path, { commitSteps, onChange: () => changes.emit() });
    prepareAreas(database);
    signalAuthority = (options.createSignalAuthority ?? createIdlePublicationAuthority)(database);
    initializeAreas(database, signalAuthority, config, clock);
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
    if (!config.test_mode && config.dispatch_enabled) {
      dispatcher = acquireLock(config.dispatcher_lock_path);
    }
    loops = prepare_background_loops({
      enable_worker_readiness: options.enableWorkerReadiness ?? false,
      test_mode: config.test_mode,
      dispatch_enabled: config.dispatch_enabled,
      dispatcher_owned: dispatcher !== null,
      before_poll: options.beforeWorkerReadinessPoll,
      worker: conversationSystem
        ? {
            connect_database: () => database!,
            conversation_system: conversationSystem,
            worker_type_registry: configured_worker_type_registry(),
            now: () => clock.nowUnix(),
          }
        : null,
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
    // Comparison mode never invokes composition factories, even when supplied.
    if (!config.test_mode) {
      const context = { database, config, clock, changes };
      if (dispatcher && options.notificationDelivery) {
        const delivery = options.notificationDelivery(context);
        background.push(delivery);
        await delivery.start();
      }
      if (options.stallChecker) {
        const checker = options.stallChecker(context);
        background.push(checker);
        await checker.start();
      }
    }
    let closing: Promise<void> | undefined;
    const close = () => {
      if (closing) return closing;
      signalAuthority?.invalidate('shutdown');
      return (closing = loops!
        .stop()
        .finally(() => closeBackground())
        .finally(() => core?.shutdown())
        .finally(() => closeAreas())
        .finally(() => app?.close())
        .finally(() => {
          database?.close();
          dispatcher?.close();
          ownership_current = false;
          ownership.close();
        })
        .catch((error) => {
          closing = undefined;
          throw error;
        }));
    };
    for (const signal of ['SIGTERM', 'SIGINT'] as const) {
      process.once(signal, () => {
        void close().catch((error) => {
          console.error(error);
          process.exitCode = 1;
        });
      });
    }
    loops.start(config.tick_seconds, changes);
    const inherited = process.env.PLAN_SERVER_LISTENER_FD;
    if (inherited !== undefined) {
      const fd = Number(inherited);
      if (!Number.isInteger(fd) || fd < 0) throw new Error('invalid PLAN_SERVER_LISTENER_FD');
      await app.listen({ fd } as FastifyListenOptions & { fd: number });
    } else {
      await app.listen({ host: '127.0.0.1', port: config.port });
    }
  } catch (error) {
    signalAuthority?.invalidate('shutdown');
    try {
      await loops?.stop();
    } catch (cleanupError) {
      console.error('Worker loop cleanup failed', cleanupError);
    }
    try {
      await closeBackground();
    } catch (cleanupError) {
      console.error('background cleanup failed', cleanupError);
    }
    try {
      await core?.shutdown();
    } catch (cleanupError) {
      console.error('conversation cleanup failed', cleanupError);
    }
    try {
      await closeAreas();
    } catch (cleanupError) {
      console.error('area cleanup failed', cleanupError);
    }
    try {
      await app?.close();
    } catch (cleanupError) {
      console.error('application cleanup failed', cleanupError);
    } finally {
      database?.close();
      dispatcher?.close();
      ownership_current = false;
      ownership.close();
    }
    throw error;
  }
}
