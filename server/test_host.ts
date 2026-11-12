/** Comparison composition. Each area appends its test-only route registration here. */
import type { Area } from './core/authority/register.ts';
import { startServer } from './core/start.ts';
import { InMemoryConversationSystem } from './conversation/in_memory_conversation_system.ts';
import { inMemoryConversationRoutes } from './conversation/testing/routes.ts';

if (process.env.PLAN_TEST_MODE !== '1') throw new Error('The comparison host requires test mode');

const conversationSystem =
  process.env.PLAN_COMPARE_CONVERSATION_SYSTEM === 'in_memory'
    ? new InMemoryConversationSystem()
    : undefined;
const testAreas: readonly Area[] = [
  ...(conversationSystem ? inMemoryConversationRoutes(conversationSystem) : []),
];

await startServer({ testAreas, conversationSystem });
