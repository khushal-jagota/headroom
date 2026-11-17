/** Codex app-server and ACP lines: one JSON-RPC message per line, ids chosen by the sender. */
import { is_record } from '../../../../core/records.ts';
import type { ReplayWireRules } from '../transcript.ts';

/** A request carries a method and an id. A notification has no id, a response no method. */
function request_id(frame: unknown): string | number | null {
  if (!is_record(frame) || typeof frame.method !== 'string') return null;
  return typeof frame.id === 'string' || typeof frame.id === 'number' ? frame.id : null;
}
/** Codex (`codex_app_server`) and Hermes (`acp`) differ only in the `jsonrpc` member. */
export const JSON_RPC_WIRE: ReplayWireRules = {
  launch_placeholders: () => ({}),
  request_method: (frame) =>
    request_id(frame) === null ? null : ((frame as { method: string }).method ?? null),
  background_reply: (request, reply) => ({
    ...(reply as Record<string, unknown>),
    id: request_id(request),
  }),
  // The driver numbers its own requests. Ids the peer chose stay literal.
  generated_values: (frame) => {
    const id = request_id(frame);
    return id === null ? [] : [id];
  },
};
