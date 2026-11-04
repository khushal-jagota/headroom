import type { NoncurrentAvailability } from './contracts';
import type { NavigationPublication } from './publication';
import type { EffectiveNavigationView } from './knowledge';
import type { NavigationRequestError } from './transport';

export type NavigationOwnerState = {
  connection: 'idle' | 'snapshot' | 'subscribed' | 'reconnecting' | 'degraded' | 'disposed';
  generation: number;
  error: NavigationRequestError | null;
  publication: NavigationPublication | undefined;
  effective: EffectiveNavigationView | undefined;
};

export type NavigationHealth = null | { state: 'reconnecting' } | { state: 'degraded'; availability: NoncurrentAvailability };
export type ScopedNavigationOwnerState = NavigationOwnerState & { health: NavigationHealth };
