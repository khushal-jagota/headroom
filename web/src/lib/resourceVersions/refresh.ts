import { queryClient } from '../queryClient';
import { createResourceCoordinator } from './coordinator';

const resources = createResourceCoordinator(queryClient);

/**
 * The one refresh path for ordinary reads: check revisions, then read what changed.
 *
 * An own write, the change signal, a reconnect and a tab return all come here. The
 * promise resolves when the bodies that the check found changed are on screen. It is
 * for a caller that must wait for the screen. Most callers do not wait.
 */
export function refreshScreen(): Promise<void> {
  return resources.refresh();
}

export function stopScreenRefresh(): void {
  resources.stop();
}
