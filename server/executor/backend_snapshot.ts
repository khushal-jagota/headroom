/** Provider snapshots become plain JSON before they enter the native executor contract. */
import type { BackendSnapshot } from '../conversation/snapshot/contracts.ts';
import { decode_json } from './protocol.ts';
import { ExecutorValidationError } from './protocol_errors.ts';
import type { WireTypes } from './wire.generated.ts';

export function snapshot_for_wire(snapshot: BackendSnapshot): WireTypes['BackendSnapshot'] {
  let text: string;
  try {
    text = JSON.stringify(snapshot);
  } catch (error) {
    throw new ExecutorValidationError(
      `Backend snapshot is not JSON: ${error instanceof Error ? error.message : String(error)}`,
    );
  }
  return decode_json(text, 'BackendSnapshot');
}
