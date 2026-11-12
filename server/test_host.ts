import { notificationTestArea } from './notifications/testing.ts';
/** Comparison composition. Each area appends its test-only route registration here. */
import { readinessTestRoutes } from './runtime/testing.ts';
import type { Area } from './core/authority/register.ts';
import { startServer } from './core/start.ts';
import { SqliteProcessConversationSystem } from './conversation/system.ts';
import { ConversationMessageFiles } from './conversation/record/message_files.ts';
import { ScriptedBackendRegistry } from './conversation/testing/scripted_registry.ts';
import { scriptedConversationControls } from './conversation/testing/scripted_controls.ts';
import { InMemoryConversationSystem } from './conversation/in_memory_conversation_system.ts';
import { inMemoryConversationRoutes } from './conversation/testing/routes.ts';
import { createIdlePublicationAuthority } from './conversation/publication/authority.ts';
import { rebuild_conversation_view } from './conversation/views/data/writer.ts';

if (process.env.PLAN_TEST_MODE !== '1') throw new Error('The comparison host requires test mode');

const conversationSystem =
  process.env.PLAN_COMPARE_CONVERSATION_SYSTEM === 'in_memory'
    ? new InMemoryConversationSystem()
    : undefined;
const registry = new ScriptedBackendRegistry();

await startServer({
  conversationSystem,
  createConversationCore: conversationSystem
    ? undefined
    : (context) => {
        const { config, database, record, clock, changes } = context;
        return new SqliteProcessConversationSystem({
          store: record.store,
          backend_child_factories: registry.factories,
          message_files: new ConversationMessageFiles(config.db_path),
          unix_time_now: () => clock.nowUnix(),
          require_runtime_ownership: context.require_runtime_ownership,
          publish_event: (event) => record.live_tail.publish_event(event),
          publish_frame: (id, frame) => record.live_tail.publish_frame(id, frame),
          publish_status_changed: (id) => record.live_tail.views.publish_status_changed(id),
          emit_change: () => changes.emit(),
          model_is_enabled: (backend_key, model_id) =>
            database.read((sql) => {
              const row = sql.get<{ enabled: number }>(
                'SELECT enabled FROM backend_model_enablement WHERE backend_key=? AND model_id=?',
                [backend_key, model_id],
              );
              return row === undefined || !!row.enabled;
            }),
        });
      },
  testAreas: (context): readonly Area[] => [
    readinessTestRoutes,
    notificationTestArea,
    ...(conversationSystem
      ? inMemoryConversationRoutes(conversationSystem)
      : scriptedConversationControls(context, registry)),
  ],
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
