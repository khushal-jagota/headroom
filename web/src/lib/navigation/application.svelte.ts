import { getContext, onDestroy, onMount, setContext } from 'svelte';
import type { ScopedNavigationOwnerState } from './owner';
import { createScopedNavigationOwner } from './views/owner';
import { navigationConfiguration } from './configuration';
import { createNavigationPresentationCache } from './presentation';
import { noteNavigationView, savedNavigationView } from '../savedSession';

const applicationContext = Symbol('navigation application');

/** One bounded view belongs to the application, above every route and conversation pane. */
export function provideNavigationApplication() {
  const owner = createScopedNavigationOwner({ saved: savedNavigationView() });
  let state = $state.raw<ScopedNavigationOwnerState>(owner.getState());
  const unsubscribe = owner.subscribe((next) => {
    state = next;
    noteNavigationView(next.publication, next.effective?.runtimeCurrent === true);
  });
  const selectors = {
    get publication(){return state.publication ?? null;},
    get records(){return state.publication?.records ?? [];},
    get effective(){return state.effective ?? null;},
    recordsOfKind(kind:string){return state.publication?.records.filter(record=>record.kind===kind) ?? [];},
  };
  const presentationFor=createNavigationPresentationCache();
  const application = {
    owner, selectors, get state() { return state; },
    presentationFor,
  };
  setContext(applicationContext, application);
  onMount(() => { void owner.start().catch(() => undefined); });
  onDestroy(() => {
    owner.dispose();
    unsubscribe();
  });
  return application;
}

export type NavigationApplication = ReturnType<typeof provideNavigationApplication>;

/** Legacy isolated component hosts have no replica. Never create a fallback owner. */
export function navigationApplication(): NavigationApplication | undefined {
  return getContext<NavigationApplication | undefined>(applicationContext);
}
