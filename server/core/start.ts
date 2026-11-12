import type { FastifyListenOptions } from 'fastify';
import { areas, commitSteps, initializeAreas, prepareAreas, closeAreas } from '../areas.ts';
import { createApp } from '../app.ts';
import { loadConfig } from './config.ts';
import { buildClock } from './clock.ts';
import { ChangeSignal } from './changes/index.ts';
import { createDatabase } from './database/index.ts';
import type { Database } from './database/index.ts';
import { acquireRuntimeOwnership, acquireLock } from './locks/index.ts';

import type { Area } from './authority/register.ts';
import { ManagerWakeLoop } from '../manager_wakes/runtime.ts';
import type { OwnedWakeContext } from '../manager_wakes/testing.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { createIdlePublicationAuthority } from '../conversation/publication/authority.ts';
import type { SignalPublicationAuthority } from '../conversation/publication/authority.ts';

/** Shared process lifecycle for the normal entry point and comparison host. */
export interface ServerOptions {
  testAreas?: readonly Area[];
  /** Factories execute only after this process owns the database. */
  ownedTestAreas?: readonly ((context: OwnedWakeContext) => Area)[];
  /** Lifecycle composition opts in explicitly. Ordinary startup never polls wakes. */
  managerWakes?: boolean;
  conversationSystem?: ConversationSystem;
  createSignalAuthority?: (database: Database) => SignalPublicationAuthority;
}
export async function startServer(options: ServerOptions = {}): Promise<void> {
  const config = loadConfig();
  const ownership = acquireRuntimeOwnership(config.db_path);
  let dispatcher: ReturnType<typeof acquireLock> = null;
  let database: ReturnType<typeof createDatabase> | undefined;
  let ownsDatabase = true;
  let managerWakes: ManagerWakeLoop | undefined;
  let unsubscribeWakes: (() => void) | undefined;
  let signalAuthority: SignalPublicationAuthority | undefined;
  try {
    const changes = new ChangeSignal();
    database = createDatabase(config.db_path, { commitSteps, onChange: () => changes.emit() });
    prepareAreas(database);
    signalAuthority = (options.createSignalAuthority ?? createIdlePublicationAuthority)(database);
    const clock = buildClock(config);
    initializeAreas(database, signalAuthority, config, clock);
    if (!config.test_mode && config.dispatch_enabled) {
      dispatcher = acquireLock(config.dispatcher_lock_path);
    }
    const assert_database_owned = () => {
      if (!ownsDatabase) throw new Error('database ownership ended');
    };
    const ownedContext = {
      database,
      clock,
      conversation_system: options.conversationSystem,
      assert_database_owned,
    };
    const ownedTestAreas = (options.ownedTestAreas ?? []).map((factory) => factory(ownedContext));
    if (options.managerWakes) {
      if (!options.conversationSystem)
        throw new Error('Manager wakes require a conversation system');
      managerWakes = new ManagerWakeLoop({
        ...ownedContext,
        conversation_system: options.conversationSystem,
      });
      unsubscribeWakes = changes.subscribe(() => managerWakes?.wake());
      managerWakes.start(config.tick_seconds);
    }
    const app = await createApp(config, {
      database,
      changes,
      areas: [...areas, ...(options.testAreas ?? []), ...ownedTestAreas],
      clock,
      signalAuthority,
      conversationSystem: options.conversationSystem,
      workspaceAdmission: options.conversationSystem?.workspace_admission,
    });
    let closing: Promise<void> | undefined;
    const close = () => {
      if (closing) return closing;
      signalAuthority?.invalidate('shutdown');
      unsubscribeWakes?.();
      return (closing = Promise.resolve(managerWakes?.stop())
        .then(() => closeAreas())
        .then(() => app.close())
        .finally(() => {
          database?.close();
          dispatcher?.close();
          ownsDatabase = false;
          ownership.close();
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
    unsubscribeWakes?.();
    await managerWakes?.stop();
    await closeAreas();
    database?.close();
    dispatcher?.close();
    ownsDatabase = false;
    ownership.close();
    throw error;
  }
}
