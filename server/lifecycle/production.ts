/** Final production switches, with one fresh composition closure for EVERY invocation. */
import { startServer } from '../core/start.ts';
import type { ConversationCompositionContext, StartedServer } from '../core/start.ts';
import { SqliteProcessConversationSystem } from '../conversation/system.ts';
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
    if (
      context.config.executor_id !== null &&
      dependencies.environments.dependencies.executor === null
    )
      throw new Error('Configured production executor bindings are required');
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
    enableWorkerReadiness: true,
    areaLoops: { managerWakes: true, measureSources: true },
    notificationDelivery: production_notification_delivery,
    stallChecker(context) {
      if (!backends) throw new Error('Production backend composition is absent');
      return production_stall_checker(backends.stall_sources)(context);
    },
    createConversationCore(context) {
      const current = composed(context);
      return new SqliteProcessConversationSystem({
        wait_registry: WAITS,
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
    },
    createBackendServices: (context) => composed(context).http,
    async createEnvironments(context) {
      const operations = await dependencies.environments.factory(
        context,
        dependencies.environments.dependencies,
      );
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
