/** Record-only runtime, initialized after the shared server acquires database ownership. */
import { dirname, join } from 'node:path';
import type { Database } from '../../core/database/contracts.ts';
import type { Config } from '../../core/config.ts';
import { SSE_HEARTBEAT_MS } from '../../core/config.ts';
import { ConversationLiveTail } from '../live_tail.ts';
import { createConversationViewRoutes } from '../views/routes.ts';
import type { ConversationViewRuntime } from '../views/routes.ts';
import { ConversationViewRebuilder } from '../views/data/rebuild.ts';
import { ConversationStore } from './data/store.ts';
import { retained_run_is_unresolved } from './data/lifecycle.ts';

export function createConversationRuntime() {
  let runtime: ConversationViewRuntime | undefined;
  let rebuilder: ConversationViewRebuilder | undefined;
  const getRuntime = (): ConversationViewRuntime => {
    if (runtime === undefined) {
      throw new Error('Conversation record runtime is not initialized');
    }
    return runtime;
  };
  return {
    getRuntime,
    routes: createConversationViewRoutes(getRuntime),
    initialize(
      database: Database,
      config: Config,
      options: { integer_now?: () => number } = {},
    ): void {
      if (runtime !== undefined) {
        throw new Error('Conversation record runtime is already initialized');
      }
      // startServer owns the canonical conversation lock before any area initializes.
      const store = new ConversationStore(database, { integer_now: options.integer_now });
      database.write((tx) => store.reconcile_in_transaction(tx, new Map()), {
        changeSignal: 'silent',
      });
      const live_tail = new ConversationLiveTail({
        view_output_directory: join(dirname(config.db_path), 'conversation-view-output'),
      });
      live_tail.views.cleanup_stale_epochs();
      runtime = {
        database,
        store,
        live_tail,
        sse_heartbeat_ms: SSE_HEARTBEAT_MS,
        async runtime_facts(conversation_id) {
          // Stored requests never recreate process-local callbacks after a restart.
          const retained = database.read((sql) => retained_run_is_unresolved(sql, conversation_id));
          return { running: retained ? null : false, permissions: [], inputs: [] };
        },
      };
      rebuilder = new ConversationViewRebuilder();
      rebuilder.start(database);
    },
    bind_runtime_facts(facts: ConversationViewRuntime['runtime_facts']): void {
      getRuntime().runtime_facts = facts;
    },
    async close(): Promise<void> {
      rebuilder?.close();
      runtime?.live_tail.close_all_subscriptions();
      await rebuilder?.finish();
      await runtime?.store.finish_writes();
      runtime = undefined;
      rebuilder = undefined;
    },
  };
}
