import { ManagerWakeLoop } from './manager_wakes/runtime.ts';
import type { OwnedAreaContext } from './core/authority/register.ts';
import { routes as managers } from './managers/routes.ts';
import { routes as messageDelivery } from './message_delivery/routes.ts';
import { configure_manager_resources } from './managers/resource_versions.ts';
import type { Clock } from './core/clock.ts';
import { routes as notifications } from './notifications/routes.ts';
import { initialize_notifications } from './notifications/data.ts';
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
import type { SignalNavigationAuthority } from './conversation/publication/authority.ts';
import type { Area } from './core/authority/register.ts';
import type { Database, CommitStep } from './core/database/index.ts';
import { routes as projects } from './projects/routes.ts';
import { routes as ideas } from './ideas/routes.ts';
import { routes as resourceVersions } from './resource_versions/routes.ts';
import { createNavigationRuntime } from './navigation/index.ts';
import { idle_publication } from './conversation/publication/idle.ts';
import { createConversationRuntime } from './conversation/record/runtime.ts';

const navigation = createNavigationRuntime(idle_publication);
const conversation = createConversationRuntime();
/** Diagnose retained Tickets before the first projection-producing commit. */
export const prepareAreas = (database: Database): void => {
  database.write((tx) => audit_ticket_registry_integrity(tx, { startup: true }));
};
export const initializeAreas = (
  database: Database,
  authority: SignalNavigationAuthority,
  config: Config,
  clock: Clock,
): void => {
  database.write((tx) => initialize_notifications(tx, clock.nowUnix()));
  initialize_worker_settings(database, config);
  configure_conversation_start(database, config);
  configure_chief_resources(config);
  configure_manager_resources(clock);
  conversation.initialize(database, config);
  navigation.initialize(database, authority);
};
export const closeAreas = async (): Promise<void> => {
  navigation.close();
  await conversation.close();
};

/** Add one area registration here. Core has no area identity branches. */
export const areas: readonly Area[] = [
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
  { prefix: '/api/navigation/view', routes: navigation.routes },
  { prefix: '/api/conversation', routes: conversation.routes },
];
/** Global order: signal publication, artifact links, navigation. */
export const commitSteps: readonly CommitStep[] = [
  navigation.publicationCommitStep,
  artifactLinks,
  navigation.navigationCommitStep,
];

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
    unsubscribe = context.changes.subscribe(() => wakes?.wake());
    wakes.start(context.config.tick_seconds);
  }
  return {
    async stop(): Promise<void> {
      unsubscribe?.();
      await wakes?.stop();
    },
  };
}
