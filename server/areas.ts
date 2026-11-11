import type { Area } from './core/authority/register.ts';
import type { Database, CommitStep } from './core/database/index.ts';
import { routes as projects } from './projects/routes.ts';
import { routes as ideas } from './ideas/routes.ts';
import { routes as resourceVersions } from './resource_versions/routes.ts';
import { createNavigationRuntime } from './navigation/index.ts';
import { idle_publication } from './conversation/publication/idle.ts';

const navigation = createNavigationRuntime(idle_publication);
export const initializeAreas = (database: Database): void => navigation.initialize(database);
export const closeAreas = (): void => navigation.close();

/** Add one area registration here. Core has no area identity branches. */
export const areas: readonly Area[] = [
  { prefix: '/api', routes: projects },
  { prefix: '/api', routes: ideas },
  { prefix: '/api', routes: resourceVersions },
  { prefix: '/api/navigation/view', routes: navigation.routes },
];
/** Global order: signal publication, artifact links, navigation. */
export const commitSteps: readonly CommitStep[] = [navigation.publicationCommitStep, navigation.navigationCommitStep];
