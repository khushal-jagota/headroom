import {
  routes as measureSources,
  admission as source_admission,
} from './measure_sources/routes.ts';
import { routes as previews } from './previews/routes.ts';
import { routes as files } from './files/routes.ts';
import { routes as measures } from './measures/routes.ts';
import { routes as datasets } from './datasets/routes.ts';
import { admission as measure_admission } from './measures/routes.ts';
import { configure_measure_resources } from './measures/resource_versions.ts';
import { routes as goals } from './goals/routes.ts';
import { routes as attention } from './attention/routes.ts';
import { configure_goal_resources } from './goals/resource_versions.ts';
import { configure_goal_manager_documents } from './goals/manager_documents.ts';
import { routes as credentials } from './credentials/routes.ts';
import { ManagerWakeLoop } from './manager_wakes/runtime.ts';
import {
  all_sender_messages_are_manager_notices,
  admit_notice_batch,
  create_ticket_failure_notice,
} from './manager_wakes/data.ts';
import { PromptDispatchDecision } from './conversation/contracts.ts';
import type { OwnedAreaContext } from './core/authority/register.ts';
import { routes as managers } from './managers/routes.ts';
import { routes as messageDelivery } from './message_delivery/routes.ts';
import { configure_manager_resources } from './managers/resource_versions.ts';
import { routes as notifications } from './notifications/routes.ts';
import { initialize_notifications } from './notifications/data.ts';
import {
  conversation_attention_snapshot,
  advance_conversation_attention,
  capture_conversation_attention,
} from './notifications/attention.ts';
import type { Clock } from './core/clock.ts';
import type { ConversationViewRuntime } from './conversation/views/routes.ts';
import { conversation_routes } from './tickets/conversation_routes.ts';
import { routes as membership } from './membership/routes.ts';
import { configure_conversation_start } from './runtime/conversation_start.ts';
import { configure_chief_resources } from './worker_settings/resource_versions.ts';
import { audit_ticket_registry_integrity } from './tickets/data/index.ts';
import { routes as tickets } from './tickets/routes.ts';
import { finalize as artifactLinks } from './files/catalog/links.ts';
import type { Config } from './core/config.ts';
import { initialize_worker_settings } from './worker_settings/startup.ts';
import { routes as workerSettings } from './worker_settings/routes.ts';
import type { Area } from './core/authority/register.ts';
import type { Database, CommitStep } from './core/database/index.ts';
import { routes as projects } from './projects/routes.ts';
import { routes as ideas } from './ideas/routes.ts';
import { routes as resourceVersions } from './resource_versions/routes.ts';
import { createSignalNavigationRuntime } from './navigation/index.ts';
import { createConversationRuntime } from './conversation/record/runtime.ts';
import { deployment_participants } from './conversation/deploy_interruptions/participants.ts';
import type { SignalPublicationCoordinator } from './conversation/publication/coordinator.ts';

/** Each server owns one reconciled record and its publication attachment. */
export function createAreasRuntime() {
  const conversation = createConversationRuntime();
  let publication: SignalPublicationCoordinator | undefined;
  let navigation: ReturnType<typeof createSignalNavigationRuntime> | undefined;
  let recordReady = false;
  let attached = false;
  const commitSteps: readonly CommitStep[] = [
    (tx) => publication?.finalize_signal(tx),
    artifactLinks,
    (tx) => {
      if (attached) navigation!.navigationCommitStep(tx);
    },
  ];
  return {
    commitSteps,
    getRuntime: conversation.getRuntime,
    bindRuntimeFacts(facts: ConversationViewRuntime['runtime_facts']): void {
      conversation.bind_runtime_facts(facts);
    },
    prepare(database: Database): void {
      database.write((tx) => audit_ticket_registry_integrity(tx, { startup: true }));
    },
    initialize(
      database: Database,
      config: Config,
      clock: Clock,
      beforeRuntime: () => void = () => {},
    ): void {
      database.write((tx) => initialize_notifications(tx, clock.nowUnix()));
      initialize_worker_settings(database, config);
      configure_conversation_start(database, config);
      configure_chief_resources(config);
      configure_manager_resources(clock);
      configure_measure_resources(clock);
      configure_goal_resources(clock);
      configure_goal_manager_documents();
      beforeRuntime();
      // The record owns the sole store, tail, reconciliation, and epoch rotation.
      conversation.initialize(database, config, {
        ...deployment_participants(config.db_path),
        integer_now: () => clock.nowUnix(),
        all_are_notices: all_sender_messages_are_manager_notices,
        failure_notice: create_ticket_failure_notice,
        admit_notice(tx, conversation_id, sender_message_id, now) {
          const [is_manager_notice, suppression_reason] = admit_notice_batch(tx, {
            conversation_id,
            sender_message_id,
            now,
          });
          return new PromptDispatchDecision({
            sender_message_id,
            is_manager_notice,
            suppression_reason,
          });
        },
        prepare_attention(tx, conversation_id) {
          const snapshot = conversation_attention_snapshot(tx, conversation_id);
          if (snapshot === null) return null;
          return (source, change) => {
            const attention = advance_conversation_attention(
              snapshot,
              change.written.map((event) => event.payload),
              {
                owner_read_through_sequence: change.owner_read_through_sequence,
                occurred_at: change.occurred_at,
              },
            );
            capture_conversation_attention(source, conversation_id, change.occurred_at, attention);
          };
        },
      });
      recordReady = true;
    },
    bindPublication(source: SignalPublicationCoordinator): void {
      publication = source;
      navigation = createSignalNavigationRuntime(source);
    },
    attachNavigation(): void {
      navigation!.attach();
      attached = true;
    },
    get areas(): readonly Area[] {
      return [
        { prefix: '', routes: previews },
        { prefix: '/api', routes: measures, admission: measure_admission },
        { prefix: '/api', routes: measureSources, admission: source_admission },
        { prefix: '/api', routes: datasets, admission: measure_admission },
        { prefix: '/api', routes: goals },
        { prefix: '/api', routes: attention },
        { prefix: '', routes: files },
        { prefix: '/api', routes: credentials },
        { prefix: '/api', routes: managers },
        { prefix: '/api', routes: messageDelivery },
        { prefix: '/api', routes: notifications },
        { prefix: '/api', routes: tickets },
        { prefix: '/api', routes: conversation_routes },
        { prefix: '/api', routes: membership },
        { prefix: '/api', routes: projects },
        { prefix: '/api', routes: ideas },
        { prefix: '/api', routes: workerSettings },
        { prefix: '/api', routes: resourceVersions },
        { prefix: '/api/navigation/view', routes: navigation!.routes },
        { prefix: '/api/conversation', routes: conversation.routes },
      ];
    },
    closeSubscriptions(): void {
      if (recordReady) conversation.getRuntime().live_tail.close_all_subscriptions();
    },
    async closeRecord(): Promise<void> {
      await conversation.close();
      recordReady = false;
    },
    detachNavigation(): void {
      navigation?.close();
      attached = false;
    },
  };
}

/** The lifecycle composer opts into production loops at this composition point. */
export interface AreaLoopOptions {
  managerWakes?: boolean;
}
export function composeAreaLoops(context: OwnedAreaContext, options: AreaLoopOptions = {}) {
  let wakes: ManagerWakeLoop | undefined;
  let unsubscribe: (() => void) | undefined;
  if (options.managerWakes) {
    context.assert_database_owned();
    if (!context.conversationSystem) throw new Error('Manager wakes require a conversation system');
    wakes = new ManagerWakeLoop({
      database: context.database,
      clock: context.clock,
      conversation_system: context.conversationSystem,
      assert_database_owned: context.assert_database_owned,
      workspace_context: {
        db_path: context.config.db_path,
        workspace_root: context.config.workspace_root,
      },
    });
  }
  return {
    start(): void {
      if (!wakes) return;
      unsubscribe = context.changes.subscribe(() => wakes?.wake());
      wakes.start(context.config.tick_seconds);
    },
    async stop(options: { deadline?: number } = {}): Promise<void> {
      unsubscribe?.();
      await wakes?.stop(options);
    },
  };
}
