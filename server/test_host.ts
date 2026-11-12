import { notificationTestArea } from './notifications/testing.ts';
/** Comparison composition. Each area appends its test-only route registration here. */
import type { Area } from './core/authority/register.ts';
import { startServer } from './core/start.ts';
import { InMemoryConversationSystem } from './conversation/in_memory_conversation_system.ts';
import { inMemoryConversationRoutes } from './conversation/testing/routes.ts';
import { createIdlePublicationAuthority } from './conversation/publication/authority.ts';
import { rebuild_conversation_view } from './conversation/views/data/writer.ts';

if (process.env.PLAN_TEST_MODE !== '1') throw new Error('The comparison host requires test mode');

const conversationSystem =
  process.env.PLAN_COMPARE_CONVERSATION_SYSTEM === 'in_memory'
    ? new InMemoryConversationSystem()
    : undefined;
const testAreas: readonly Area[] = [
  notificationTestArea,
  ...(conversationSystem ? inMemoryConversationRoutes(conversationSystem) : []),
];

await startServer({
  testAreas,
  conversationSystem,
  createSignalAuthority(database) {
    // Rebuild copied Python projections through the actual TS writer. Comparing
    // copied rows alone would leave writer drift hidden behind the fixture oracle.
    // This existing test-host bootstrap seam produces no HTTP event writes.
    database.write(
      (tx) => {
        const records = tx.all<{ conversation_id: string }>(
          'SELECT conversation_id FROM conversations WHERE latest_sequence>0 ORDER BY conversation_id',
        );
        for (const { conversation_id } of records) {
          rebuild_conversation_view(tx, conversation_id);
        }
      },
      { changeSignal: 'silent' },
    );
    return createIdlePublicationAuthority(database);
  },
});
