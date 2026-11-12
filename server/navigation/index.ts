import { createNavigationRegistry } from './data/registry.ts';
import { createNavigationPublication } from './publication.ts';
import type { PublicationFactory } from './contracts.ts';
import { createRoutes } from './routes.ts';
import { installSignal, participateSignal } from './publication.ts';
import type { SignalAttachment, SignalPublicationSource } from '../conversation/contracts.ts';

/** Attachment lifecycle stays separate from runtime ownership and record recovery. */
export function createSignalNavigationRuntime(source: SignalPublicationSource) {
  const navigation = createNavigationRegistry();
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
    routes: createRoutes(),
  };
}
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
