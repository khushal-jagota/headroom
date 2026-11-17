/** Local metadata remains in the spool wrapper and never enters the submitted event. */
import type { ItemRow, OutboxDeliveryState } from './contracts.ts';

interface StoredPayload {
  payload: unknown;
  files: { owner_id: string; relative_path: string; sha256: string }[];
  delivery?: OutboxDeliveryState;
}
export function stored_payload(row: Pick<ItemRow, 'payload_json'>): StoredPayload {
  return JSON.parse(row.payload_json.toString('utf8')) as StoredPayload;
}
export function delivery_state(row: Pick<ItemRow, 'payload_json'>): OutboxDeliveryState {
  return (
    stored_payload(row).delivery ?? {
      started_at_ms: null,
      file_refusals: [],
      failure: null,
    }
  );
}
export function delivery_diagnostics(row: Pick<ItemRow, 'payload_json'>): boolean {
  const state = delivery_state(row);
  return state.failure !== null || state.file_refusals.length !== 0;
}
/** JSON functions accept text on every supported SQLite version. */
export const NO_DELIVERY_DIAGNOSTICS =
  "COALESCE(json_extract(CAST(payload_json AS TEXT),'$.delivery.failure'),'')='' AND COALESCE(json_array_length(CAST(payload_json AS TEXT),'$.delivery.file_refusals'),0)=0";
