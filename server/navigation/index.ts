import { createNavigationRegistry } from './data/registry.ts';
import { createNavigationPublication } from './publication.ts';
import type { PublicationFactory } from './contracts.ts';
import { createRoutes } from './routes.ts';
/** Composition injects conversation facts. Storage, permits, and delivery remain navigation-owned. */
export function createNavigationRuntime(publicationFactory: PublicationFactory) {
  const navigation = createNavigationRegistry();
  const publication = createNavigationPublication(publicationFactory, navigation);
  return {
    publicationCommitStep: publication.commitStep,
    navigationCommitStep: navigation.finalize,
    initialize: publication.initialize,
    close: publication.close,
    routes: createRoutes(),
  };
}
export { createNavigationRegistry } from './data/registry.ts';
export { createRoutes } from './routes.ts';
export type { NavigationAuthority } from './contracts.ts';
export type { PublicationFactory } from './contracts.ts';
