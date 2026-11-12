/** Export-only receipt proof. The driver supplies every row and payload. */
import { createDatabase, registerReadFunction } from '../../../core/database/index.ts';
import type { ConversationEventKind, ConversationEventPayload } from '../../../conversation/events.ts';
import {
  conversation_event_payload_from_canonical_json,
  conversation_event_payload_to_canonical_json,
} from '../../../conversation/record/codec.ts';
import * as data from '../data.ts';
import type { RequestDeliveryStatus } from '../contracts.ts';
interface EventSpec {
  kind: ConversationEventKind;
  payload: string;
}
interface Operation {
  name: string;
  op: 'observe' | 'settle' | 'reconcile';
  conversation_id?: string;
  events?: readonly EventSpec[];
  attempt_id?: string;
  status?: RequestDeliveryStatus;
  reason?: string | null;
}
interface Spec {
  db_path: string;
  now: number;
  operations: readonly Operation[];
}
function event_json(payload: ConversationEventPayload) {
  return { kind: payload.kind, payload: conversation_event_payload_to_canonical_json(payload) };
}
export function run(spec: Spec): unknown {
  const database = createDatabase(spec.db_path);
  const answers: unknown[] = [];
  try {
    for (const operation of spec.operations) {
      const result = database.write((tx) => {
        registerReadFunction(tx, 'unixepoch', () => spec.now);
        if (operation.op === 'observe') {
          const payloads = (operation.events ?? []).map((event) =>
            conversation_event_payload_from_canonical_json(event.kind, event.payload));
          return data.observe_events(tx, operation.conversation_id!, payloads).map(event_json);
        }
        if (operation.op === 'settle') {
          const event = data.settle_attempt(tx, operation.attempt_id!, operation.status!, operation.reason ?? null, spec.now);
          return event === null ? null : event_json(event);
        }
        return Object.fromEntries([...data.reconcile_deliveries(tx)].map(([key, events]) => [key, events.map(event_json)]));
      });
      const rows = database.read((sql) => ({
        requests: sql.all('SELECT * FROM credential_requests ORDER BY request_id'),
        attempts: sql.all('SELECT * FROM credential_request_delivery_attempts ORDER BY attempt_id'),
      }));
      answers.push({ name: operation.name, result, ...rows });
    }
    return { answers };
  } finally { database.close(); }
}
