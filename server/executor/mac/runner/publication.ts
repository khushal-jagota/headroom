/** Simple command consequences use the existing spool append and validation transaction. */
import { decode_json, decode_value } from '../../protocol.ts';
import { is_record as is_json_object } from '../../../core/records.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import type { CaptureRows } from '../storage/contracts.ts';
import type { MacOutputPublication } from './command_contracts.ts';

export function create_mac_output_publication(outbox: OutboxStore): MacOutputPublication {
  return {
    async add(entry, options) {
      const append = (capture: CaptureRows) => {
        const manifests = capture.files.map((file) =>
          decode_value(
            {
              scope: 'ticket',
              owner_id: file.ticket_id,
              relative_path: file.relative_path,
              byte_count: file.byte_count,
              sha256: file.sha256,
            },
            'FileManifest',
          ),
        );
        const child = is_json_object(entry.payload.child)
          ? entry.payload.child
          : is_json_object(entry.payload.ask)
            ? entry.payload.ask.child
            : null;
        const child_id =
          is_json_object(child) && typeof child.child_id === 'string' ? child.child_id : '';
        const payload = Buffer.from(JSON.stringify({ payload: entry.payload, files: manifests }));
        outbox.append(
          {
            submission_id: entry.submission_id,
            conversation_id: entry.conversation_id,
            child_id,
            kind: entry.kind,
          },
          payload,
          capture.files,
          capture.updates,
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
                file_dependencies: manifests,
              }),
              'QueuedEnvelope',
            );
          },
        );
      };
      if (options?.ticket_id !== undefined) {
        if (entry.submission_id !== undefined && outbox.lookup(entry.submission_id) !== null)
          return;
        // Capture owns copy/hash work outside the writer. Its append callback stays synchronous.
        await outbox.with_capture(options.ticket_id, append);
      } else append({ files: [], updates: [] });
    },
  };
}
