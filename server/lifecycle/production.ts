/** Final production switches, with one fresh composition closure for EVERY invocation. */
import { startServer } from '../core/start.ts';
import type { ConversationCompositionContext, StartedServer } from '../core/start.ts';
import { VpsExecutor } from '../executor/vps_transport.ts';
import { now_ms } from '../executor/clock.ts';
import { close_run_and_record_mac_process_interruption } from '../executor/interruption_coordinator.ts';
import { close_mac_interruption_of_turn } from '../conversation/deploy_interruptions/data.ts';
import { recover_conversations } from '../conversation/deploy_interruptions/coordinator.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
import { PromptDeliveryMode } from '../conversation/contracts.ts';
import type { ConversationCoreOptions } from '../conversation/runtime/contracts.ts';
import type { Database } from '../core/database/contracts.ts';
import { ConversationMessageFiles } from '../conversation/record/message_files.ts';
import { model_is_enabled } from '../conversation/backend_state.ts';
import { createEnvironmentRuntime } from '../environments/runtime.ts';
import type {
  ProductionEnvironmentFactory,
  ProductionEnvironmentDependencies,
} from '../environments/production_contracts.ts';
import { production_notification_delivery, production_stall_checker } from './background.ts';
import { write_held_ticket_configuration } from '../runtime/conversation_start.ts';
import { WAITS } from '../stalls/registry.ts';
import { compose_production_backends, require_backend_bindings } from './production_bindings.ts';
import type { ProductionBackendBindings } from './production_bindings.ts';
import type { ShutdownCompletion } from './contracts.ts';

export interface ProductionServerDependencies {
  readonly backends: Partial<ProductionBackendBindings>;
  readonly environments: {
    readonly factory: ProductionEnvironmentFactory;
    readonly dependencies: ProductionEnvironmentDependencies;
  };
}
export async function start_production_server(
  dependencies: ProductionServerDependencies,
  onShutdownComplete?: ShutdownCompletion,
): Promise<StartedServer> {
  const bindings = require_backend_bindings(dependencies.backends);
  if (
    typeof dependencies.environments?.factory !== 'function' ||
    !dependencies.environments.dependencies ||
    dependencies.environments.dependencies.executor === undefined
  )
    throw new Error('Production environment and executor bindings are required');
  let backends: ReturnType<typeof compose_production_backends> | undefined;
  let owner: ConversationCompositionContext['database'] | undefined;
  const composed = (context: ConversationCompositionContext) => {
    if (owner !== undefined && owner !== context.database)
      throw new Error('Production composition belongs to another server invocation');
    if (backends === undefined) {
      owner = context.database;
      backends = compose_production_backends(context, bindings);
    }
    return backends;
  };
  return startServer({
    onShutdownComplete,
    createExecutor(context) {
      return context.config.executor_id === null
        ? null
        : new VpsExecutor(context.database, context.clock, {
            db_path: context.config.db_path,
            changes: context.changes,
            media_signal: context.media_signal,
            machine_id: context.config.executor_id,
            application_revision: context.config.app_sha ?? 'unknown',
          });
    },
    enableWorkerReadiness: true,
    areaLoops: { managerWakes: true, measureSources: true },
    notificationDelivery: production_notification_delivery,
    stallChecker(context) {
      if (!backends) throw new Error('Production backend composition is absent');
      return production_stall_checker(backends.stall_sources)(context);
    },
    createConversationCore: (context) =>
      create_production_conversation_core(context, composed(context)),
    createBackendServices: (context) => composed(context).http,
    async createEnvironments(context) {
      const operations = await dependencies.environments.factory(context, {
        ...dependencies.environments.dependencies,
        executor: context.executor ?? null,
      });
      const runtime = await createEnvironmentRuntime(context, operations);
      return {
        areas: runtime.areas,
        resourceProviders: runtime.resourceProviders,
        before_worker_readiness_poll: () => runtime.before_worker_readiness_poll(),
        start: () => runtime.start(),
        close: (options) => runtime.close(options),
        settled: () => runtime.settled(),
      };
    },
  });
}

/** The production core and its executor recovery callbacks share one invocation. */
export function create_production_conversation_core(
  context: ConversationCompositionContext,
  current: ReturnType<typeof compose_production_backends>,
): SqliteProcessConversationSystem {
  let system!: SqliteProcessConversationSystem;
  system = new SqliteProcessConversationSystem({
    wait_registry: WAITS,
    remote_recovery: executor_remote_recovery(context.database, () => system),
    store: context.record.store,
    backend_child_factories: current.factories,
    execution_refusal: current.execution_refusal,
    model_is_enabled: (backend, model) =>
      context.database.read((sql) => model_is_enabled(sql, backend, model)),
    message_files: new ConversationMessageFiles(context.config.db_path),
    unix_time_now: () => context.clock.nowUnix(),
    require_runtime_ownership: context.require_runtime_ownership,
    require_signal_current: () => context.publication.current_read(),
    save_attempt: context.publication.save_attempt,
    on_shutdown: context.revoke_signal,
    on_held_model_change: (conversation_id, model, reasoning_effort) =>
      write_held_ticket_configuration(
        context.database,
        conversation_id,
        model,
        reasoning_effort,
        context.clock.nowUnix(),
      ),
    publish_event: (event) => context.record.live_tail.publish_event(event),
    publish_frame: (id, frame) => context.record.live_tail.publish_frame(id, frame),
    publish_status_changed: (id) => context.record.live_tail.views.publish_status_changed(id),
    emit_change: () => context.changes.emit(),
  });
  return system;
}

/** Shared by the real core constructor and scriptable invocation hosts. */
export function executor_remote_recovery(
  database: Database,
  system: () => SqliteProcessConversationSystem,
): NonNullable<ConversationCoreOptions['remote_recovery']> {
  return {
    retire_replaced: (reference, replaced_by, undelivered_message) =>
      close_run_and_record_mac_process_interruption(database, reference, replaced_by, now_ms(), {
        undelivered_message,
      }),
    close_rewritten: (conversation_id, turn_number, note) =>
      database.write((tx) =>
        close_mac_interruption_of_turn(
          tx,
          conversation_id,
          turn_number,
          Math.floor(now_ms() / 1000),
          note,
        ),
      ),
    recover: (conversation_id) =>
      recover_conversations(system(), database, {
        conversation_ids: [conversation_id],
        mode: PromptDeliveryMode.send_now,
      }),
  };
}
