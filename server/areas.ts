import { routes as previews } from './previews/routes.ts';
import { routes as files } from './files/routes.ts';
import { routes as credentials } from './credentials/routes.ts';
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
): void => {
  initialize_worker_settings(database, config);
  configure_conversation_start(database, config);
  configure_chief_resources(config);
  conversation.initialize(database, config);
  navigation.initialize(database, authority);
};
export const closeAreas = async (): Promise<void> => {
  navigation.close();
  await conversation.close();
};

/** Add one area registration here. Core has no area identity branches. */
export const areas: readonly Area[] = [
  { prefix: '', routes: previews },
  { prefix: '', routes: files },
  { prefix: '/api', routes: credentials },
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
