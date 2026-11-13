import { createNavigationRegistry } from './data/registry.ts';
import { createRoutes } from './routes.ts';
import { installSignal, participateSignal } from './publication.ts';
import type { SignalAttachment, SignalPublicationSource } from '../conversation/contracts.ts';

/** Attachment lifecycle stays separate from runtime ownership and record recovery. */
export function createSignalNavigationRuntime(source: SignalPublicationSource) {
  const navigation = createNavigationRegistry();
  const delivery = createRoutes();
  let attachment: SignalAttachment | null = null;
  return {
    navigationCommitStep: navigation.finalize,
    attach(): SignalAttachment {
      if (attachment) throw new Error('Signal navigation is already attached');
      const installed = source.attach_signal_publication({
        install: (tx, snapshot) => installSignal(tx, snapshot, navigation),
        participate: participateSignal,
        unavailable: () => {},
      });
      installed.validate(installed.capture_permit());
      attachment = installed;
      return installed;
    },
    close(): void {
      attachment?.close();
      attachment = null;
    },
    routes: delivery.routes,
    start: () => delivery.start(),
  };
}
export { createNavigationRegistry } from './data/registry.ts';
export { createRoutes } from './routes.ts';
export type { NavigationAuthority } from './contracts.ts';
