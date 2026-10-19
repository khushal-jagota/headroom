import { getContext, onDestroy, onMount, setContext } from 'svelte';
import { createNavigationCollection } from './collection';
import { createNavigationOwner, type NavigationOwnerState } from './owner';
import { createNavigationSelectors } from './selectors.svelte';
import { createNavigationTransport } from './transport';
import { createCommandEvidence } from './commandEvidence';
import { navigationConfiguration } from './configuration';
import { createNavigationPresentationCache } from './presentation';
export type { RecordImpact,CommandImpact,CommandEvidence,ResponseMetadata } from './commandEvidence';

const applicationContext = Symbol('navigation application');

/** One replica belongs to the application, above every route and conversation pane. */
export function provideNavigationApplication() {
  const collection = createNavigationCollection();
  const owner = createNavigationOwner({ adapter: collection, transport: createNavigationTransport() });
  let state = $state.raw<NavigationOwnerState>(owner.getState());
  const unsubscribe = owner.subscribe((next) => { state = next; });
  const selectors = createNavigationSelectors(collection, () => state);
  const commands=createCommandEvidence(owner,navigationConfiguration.maximumCommandEvidence);
  const presentationFor=createNavigationPresentationCache();
  const application = {
    owner, selectors, get state() { return state; },
    presentationFor,
    registerRenderBoundary:commands.register,
    disposeRenderBoundary:commands.dispose,
    captureRenderBoundaries:commands.capture,
    runCommand:commands.runCommand,
    get commandEvidence(){return commands.evidence;},
    subscribeCommandEvidence:commands.subscribe,
  };
  setContext(applicationContext, application);
  onMount(() => { void owner.start().catch(() => undefined); });
  onDestroy(() => {
    commands.clear();
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
