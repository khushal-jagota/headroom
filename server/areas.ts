import type { Area } from './core/authority/register.ts';
import type { CommitStep } from './core/database/index.ts';
import { routes as projects } from './projects/routes.ts';
import { routes as ideas } from './ideas/routes.ts';
import { routes as resourceVersions } from './resource_versions/routes.ts';
import { routes as navigation, navigationCommitStep } from './navigation/index.ts';

/** Add one area registration here. Core has no area identity branches. */
export const areas: readonly Area[] = [
  { prefix: '/api', routes: projects },
  { prefix: '/api', routes: ideas },
  { prefix: '/api', routes: resourceVersions },
  { prefix: '/api/navigation/view', routes: navigation },
];
/** Global order: signal publication, artifact links, navigation. */
export const commitSteps: readonly CommitStep[] = [navigationCommitStep];
