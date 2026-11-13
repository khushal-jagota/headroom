/** Simple command consequences use the existing spool append and validation transaction. */
import { stringify_python_json } from '../../../core/http/numeric_json.ts';
import { decode_json, decode_value } from '../../protocol.ts';
import { normalize_integer } from '../../integers.ts';
import type { OutboxStore } from '../storage/outbox.ts';
import type { CaptureRows } from '../storage/contracts.ts';
import type { MacOutputPublication } from './command_contracts.ts';
function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

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
              byte_count: normalize_integer(file.byte_count),
              sha256: file.sha256,
            },
            'FileManifest',
          ),
        );
        const child = object(entry.payload.child)
          ? entry.payload.child
          : object(entry.payload.ask)
            ? entry.payload.ask.child
            : null;
        const child_id = object(child) && typeof child.child_id === 'string' ? child.child_id : '';
        const payload = Buffer.from(
          stringify_python_json(
            { payload: entry.payload, files: manifests },
            {
              sort_keys: true,
              compact: true,
              ensure_ascii: true,
            },
          ),
        );
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
              stringify_python_json({
                sender_id: outbox.sender_id,
                conversation_id: row.conversation_id,
                sequence: normalize_integer(row.sequence),
                submission_id: row.submission_id,
                kind: row.kind,
                payload: entry.payload,
                file_dependencies: manifests,
              }),
              'QueuedEnvelope',
              { strict: true },
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
