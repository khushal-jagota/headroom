/** Hermes' card boundary. */
import type { CommandOutcome } from '../../../conversation/snapshot/contracts.ts';
/** Hermes' retained Python inventory script reports its own JSON through this door. */
export interface HermesModelCatalogSource {
  (options: { refresh: boolean }): Promise<CommandOutcome>;
}
