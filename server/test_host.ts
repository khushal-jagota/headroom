import { sourceTestArea } from './measure_sources/testing/routes.ts';
import { notificationTestArea } from './notifications/testing.ts';
/** Comparison composition. Each area appends its test-only route registration here. */
import { managerWakeTestArea } from './manager_wakes/testing.ts';
import { readinessTestRoutes } from './runtime/testing.ts';
import type { Area } from './core/authority/register.ts';
import { startServer } from './core/start.ts';
import { write_held_ticket_configuration } from './runtime/conversation_start.ts';
import { SqliteProcessConversationSystem } from './conversation/system.ts';
import { WAITS } from './stalls/registry.ts';
import { ConversationMessageFiles } from './conversation/record/message_files.ts';
import { ScriptedBackendRegistry } from './conversation/testing/scripted_registry.ts';
import { scriptedConversationControls } from './conversation/testing/scripted_controls.ts';
import { browserConversationControls } from './conversation/testing/browser_controls.ts';
import { ScriptedTicketChoiceFault } from './conversation/testing/scripted_ticket_choice.ts';
import { InMemoryConversationSystem } from './conversation/in_memory_conversation_system.ts';
import { inMemoryConversationRoutes } from './conversation/testing/routes.ts';
import { rebuild_conversation_view } from './conversation/views/data/writer.ts';
import { create_test_environment_runtime } from './environments/testing/composition.ts';
import {
  create_backend_runtime,
  unavailable_backend_providers,
} from './conversation/snapshot/composition.ts';
import { TicketModelSnapshots } from './conversation/snapshot/testing/ticket_models.ts';
import { makeUsageService } from './conversation/usage/composition.ts';
import { model_is_enabled } from './conversation/backend_state.ts';
import {
  canonical_ticket_execution_refusal,
  conversation_execution_refusal,
} from './tickets/execution_admission.ts';

if (!(await import('./core/config.ts')).testModeFromEnvironment())
  throw new Error('The comparison host requires test mode');

const conversationSystem =
  process.env.PLAN_COMPARE_CONVERSATION_SYSTEM === 'in_memory'
    ? new InMemoryConversationSystem()
    : undefined;
const registry = new ScriptedBackendRegistry();
const ticket_choice_fault = new ScriptedTicketChoiceFault();

await startServer({
  createUsageService: ({ config, clock }) =>
    makeUsageService(config, { now: () => clock.now(), ratesFetcher: () => null }),
  createEnvironments: create_test_environment_runtime,
  createBackendServices(context) {
    const runtime = create_backend_runtime({
      database: context.database,
      config: context.config,
      now: () => context.clock.now(),
      providers: unavailable_backend_providers(),
    });
    const snapshots = new TicketModelSnapshots();
    snapshots.model = 'gpt-6.1-sol';
    snapshots.models = ['gpt-6.1-sol'];
    runtime.backend_snapshots = snapshots;
    return runtime;
  },
  conversationSystem,
  createConversationCore: conversationSystem
    ? undefined
    : (context) => {
        const { config, database, record, clock, changes } = context;
        return new SqliteProcessConversationSystem({
          store: record.store,
          execution_refusal: (id) =>
            database.read((sql) =>
              conversation_execution_refusal(sql, id, canonical_ticket_execution_refusal),
            ),
          wait_registry: WAITS,
          backend_child_factories: registry.factories,
          message_files: new ConversationMessageFiles(config.db_path),
          unix_time_now: () => clock.nowUnix(),
          monotonic_now: () => clock.nowUnix(),
          require_runtime_ownership: context.require_runtime_ownership,
          require_signal_current: () => context.publication.current_read(),
          save_attempt: context.publication.save_attempt,
          on_shutdown: context.revoke_signal,
          publish_event: (event) => record.live_tail.publish_event(event),
          publish_frame: (id, frame) => record.live_tail.publish_frame(id, frame),
          publish_status_changed: (id) => record.live_tail.views.publish_status_changed(id),
          emit_change: () => changes.emit(),
          on_held_model_change: ticket_choice_fault.wrap(
            (conversation_id, model, reasoning_effort) =>
              write_held_ticket_configuration(
                database,
                conversation_id,
                model,
                reasoning_effort,
                clock.nowUnix(),
              ),
          ),
          model_is_enabled: (backend_key, model_id) =>
            database.read((sql) => model_is_enabled(sql, backend_key, model_id)),
        });
      },
  testAreas: (context): readonly Area[] => [
    readinessTestRoutes(context.shutdown_options),
    sourceTestArea({ ...context, assert_database_owned: context.require_runtime_ownership }),
    notificationTestArea,
    managerWakeTestArea(
      { ...context, assert_database_owned: context.require_runtime_ownership },
      context.shutdown_options,
    ),
    ...(conversationSystem
      ? inMemoryConversationRoutes(conversationSystem)
      : [
          ...scriptedConversationControls(context, registry, ticket_choice_fault),
          ...browserConversationControls(context, registry),
        ]),
  ],
  beforePublication(database) {
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
  },
});
