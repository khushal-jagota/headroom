import { createNavigationRegistry } from './data/registry.ts';
import { projectsSource } from './sources/projects.ts';
import { chiefSource } from './sources/baseline.ts';
import { createNavigationPublication } from './publication.ts';
import type { PublicationFactory } from './contracts.ts';
import type { NavigationSource } from './contracts.ts';
import { createRoutes } from './routes.ts';
/** Later areas append their source here. Rebuild and commit use the same registry. */
export const navigationSources: readonly NavigationSource[] = [chiefSource, projectsSource];
/** Composition injects conversation facts. Storage, permits, and delivery remain navigation-owned. */
export function createNavigationRuntime(publicationFactory: PublicationFactory, sources = navigationSources) {
  const navigation = createNavigationRegistry(sources);
  const publication = createNavigationPublication(publicationFactory, navigation);
  return {
    publicationCommitStep: publication.commitStep,
    navigationCommitStep: navigation.finalize,
    initialize: publication.initialize,
    close: publication.close,
    authority: publication.authority,
    routes: createRoutes(publication.authority, sources),
  };
}
export { createNavigationRegistry } from './data/registry.ts';
export { createRoutes } from './routes.ts';
export type { NavigationSource, NavigationAuthority } from './contracts.ts';
export type { PublicationFactory } from './contracts.ts';
