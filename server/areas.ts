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

const navigation = createNavigationRuntime(idle_publication);
export const initializeAreas = (database: Database, authority: SignalNavigationAuthority, config: Config): void => {
  initialize_worker_settings(database, config);
  database.write(tx => audit_ticket_registry_integrity(tx, {startup:true}));
  navigation.initialize(database, authority);
};
export const closeAreas = (): void => navigation.close();

/** Add one area registration here. Core has no area identity branches. */
export const areas: readonly Area[] = [
  { prefix: '/api', routes: tickets },
  { prefix: '/api', routes: projects },
  { prefix: '/api', routes: ideas },
  { prefix: '/api', routes: workerSettings },
  { prefix: '/api', routes: resourceVersions },
  { prefix: '/api/navigation/view', routes: navigation.routes },
];
/** Global order: signal publication, artifact links, navigation. */
export const commitSteps: readonly CommitStep[] = [navigation.publicationCommitStep, artifactLinks, navigation.navigationCommitStep];
