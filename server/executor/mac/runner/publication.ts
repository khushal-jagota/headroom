/** Simple command consequences use the existing spool append and validation transaction. */
import { decode_json } from '../../protocol.ts';
import { is_record as is_json_object } from '../../../core/records.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import type { MacOutputPublication } from './command_contracts.ts';

export function create_mac_output_publication(outbox: OutboxStore): MacOutputPublication {
  return {
    async add(entry, options) {
      const child = is_json_object(entry.payload.child)
        ? entry.payload.child
        : is_json_object(entry.payload.ask)
          ? entry.payload.ask.child
          : null;
      const child_id =
        is_json_object(child) && typeof child.child_id === 'string' ? child.child_id : '';
      const payload = Buffer.from(JSON.stringify({ payload: entry.payload, files: [] }));
      outbox.append(
        {
          submission_id: entry.submission_id,
          conversation_id: entry.conversation_id,
          child_id,
          kind: entry.kind,
        },
        payload,
        (row) => {
          // The spool owns the size guard. Validate the complete allocated envelope here.
          decode_json(
            JSON.stringify({
              sender_id: outbox.sender_id,
              conversation_id: row.conversation_id,
              sequence: row.sequence,
              submission_id: row.submission_id,
              kind: row.kind,
              payload: entry.payload,
            }),
            'QueuedEnvelope',
          );
        },
      );
      // The item is queued. Its Ticket's files follow separately and never delay it.
      if (options?.ticket_id !== undefined) outbox.note_ticket_activity(options.ticket_id);
    },
  };
}
