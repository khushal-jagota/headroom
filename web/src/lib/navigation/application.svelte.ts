import { getContext, onDestroy, onMount, setContext } from 'svelte';
import { createNavigationCollection } from './collection';
import { createNavigationOwner, type NavigationOwnerState } from './owner';
import { createNavigationSelectors } from './selectors.svelte';
import { createNavigationTransport } from './transport';

const applicationContext = Symbol('navigation application');

/** One replica belongs to the application, above every route and conversation pane. */
export function provideNavigationApplication() {
  const collection = createNavigationCollection();
  const owner = createNavigationOwner({ adapter: collection, transport: createNavigationTransport() });
  const selectors = createNavigationSelectors(collection);
  let state = $state.raw<NavigationOwnerState>(owner.getState());
  const unsubscribe = owner.subscribe((next) => { state = next; });
  const application = { owner, selectors, get state() { return state; } };
  setContext(applicationContext, application);
  onMount(() => { void owner.start().catch(() => undefined); });
  onDestroy(() => {
    owner.dispose();
    collection.destroy();
    unsubscribe();
  });
  return application;
}

export type NavigationApplication = ReturnType<typeof provideNavigationApplication>;

/** Legacy isolated component hosts have no replica. Never create a fallback owner. */
export function navigationApplication(): NavigationApplication | undefined {
  return getContext<NavigationApplication | undefined>(applicationContext);
}
