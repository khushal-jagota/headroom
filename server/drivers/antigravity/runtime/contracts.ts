/** What the card's model catalog reads from a throwaway agy session. */
import type { SessionConfigOption } from '@agentclientprotocol/sdk/unstable';

export type AntigravityCatalogRead =
  | { readonly signed_in: false }
  | {
      readonly signed_in: true;
      /** The session's config options as `session/new` returned them. */
      readonly initial: readonly SessionConfigOption[];
      /**
       * The config options after the session was put on each other model. agy reports the
       * thinking levels of the selected model only. A model whose selection failed is absent.
       */
      readonly after_selecting: ReadonlyMap<string, readonly SessionConfigOption[]>;
    };
/** Reads what the agy at that path can run. Throws when it does not answer. */
export type AntigravityCatalogSource = (executable: string) => Promise<AntigravityCatalogRead>;
