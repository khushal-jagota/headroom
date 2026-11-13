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
import type { ConversationStoreOptions } from './contracts.ts';

export function createConversationRuntime() {
  let runtime: ConversationViewRuntime | undefined;
  let rebuilder: ConversationViewRebuilder | undefined;
  let sealed = false;
  const getRuntime = (): ConversationViewRuntime => {
    if (runtime === undefined) {
      throw new Error('Conversation record runtime is not initialized');
    }
    return runtime;
  };
  return {
    getRuntime,
    routes: createConversationViewRoutes(getRuntime),
    initialize(database: Database, config: Config, options: ConversationStoreOptions = {}): void {
      if (runtime !== undefined) {
        throw new Error('Conversation record runtime is already initialized');
      }
      // startServer owns the canonical conversation lock before any area initializes.
      const store = new ConversationStore(database, options);
      database.write((tx) => store.reconcile_in_transaction(tx, new Map()), {
        changeSignal: 'silent',
      });
      const live_tail = new ConversationLiveTail({
        view_output_directory: join(dirname(config.db_path), 'conversation-view-output'),
      });
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
    },
    start_rebuild(admission?: { readonly closed: boolean }): void {
      if (sealed) throw new Error('Conversation record runtime is shut down');
      if (rebuilder) throw new Error('Conversation record replay already started');
      const current = getRuntime();
      current.live_tail.views.cleanup_stale_epochs();
      rebuilder = new ConversationViewRebuilder();
      rebuilder.start(current.database, () => admission?.closed ?? false);
    },
    bind_runtime_facts(facts: ConversationViewRuntime['runtime_facts']): void {
      getRuntime().runtime_facts = facts;
    },
    seal(): void {
      sealed = true;
      rebuilder?.close();
      runtime?.live_tail.close_all_subscriptions();
    },
    async close(): Promise<void> {
      sealed = true;
      rebuilder?.close();
      runtime?.live_tail.close_all_subscriptions();
      await rebuilder?.finish();
      await runtime?.store.finish_writes();
      runtime?.live_tail.close();
      runtime = undefined;
      rebuilder = undefined;
    },
  };
}
