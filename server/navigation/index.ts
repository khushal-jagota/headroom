import { createNavigationRegistry } from './data/registry.ts';
import { projectsSource } from './sources/projects.ts';
import { chiefSource } from './sources/baseline.ts';
/** Later areas append their source here. Rebuild and commit use the same registry. */
export const navigationSources = [chiefSource, projectsSource];
export const navigation = createNavigationRegistry(navigationSources);
export const navigationCommitStep = navigation.finalize;
export const rebuildNavigation = navigation.rebuild;
export { configureNavigationAuthority, routes } from './routes.ts';
export { createNavigationRegistry } from './data/registry.ts';
export type { NavigationSource, NavigationAuthority } from './contracts.ts';
