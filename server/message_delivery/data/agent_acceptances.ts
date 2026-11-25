/** Home owns these records and file bytes independently of native delivery. */
import { createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import type { WireValue } from '../../executor/contracts.ts';
import { as_object, decode_value } from '../../executor/protocol.ts';
import { regular_descriptor } from '../../executor/files.ts';
import type { PublishedFiles } from '../../executor/data/inbox.ts';
import type { AcceptedAgentMessage, RetainedMessageFile } from '../contracts.ts';
import { MAX_PENDING_AGENT_MESSAGES, MAX_PENDING_AGENT_MESSAGE_BYTES } from '../configuration.ts';

function decode_record(text: string): AcceptedAgentMessage {
  const stored = JSON.parse(text) as AcceptedAgentMessage;
  return {
    ...stored,
    envelope: decode_value(stored.envelope, 'QueuedEnvelope'),
    receipt: decode_value(stored.receipt, 'AcceptanceReceipt'),
  };
}
function request(envelope: WireValue<'QueuedEnvelope'>): unknown {
  const { sequence: _sequence, ...immutable } = as_object(envelope, 'QueuedEnvelope');
  return immutable;
}
export function read_agent_message(
  sql: ReadSql,
  submission_id: string,
): AcceptedAgentMessage | null {
  const row = sql.get<{ record_json: string }>(
    'SELECT record_json FROM agent_message_acceptances WHERE submission_id=?',
    [submission_id],
  );
  return row ? decode_record(row.record_json) : null;
}
/** Authentication precedes this comparison. An ID never permits another source to read a receipt. */
export function replay_agent_message(
  sql: ReadSql,
  envelope: WireValue<'QueuedEnvelope'>,
  machine_id: string,
  source: Principal,
): AcceptedAgentMessage | null {
  const previous = read_agent_message(sql, envelope.submission_id);
  if (previous === null) return null;
  if (
    previous.machine_id !== machine_id ||
    previous.sender_id !== envelope.sender_id ||
    previous.source_conversation_id !== envelope.conversation_id ||
    !isDeepStrictEqual(previous.source, source)
  )
    throw new PlannerError('agent_forbidden', 'message receipt belongs to another source');
  if (!isDeepStrictEqual(request(previous.envelope), request(envelope)))
    throw new PlannerError('validation', 'Submission identity conflicts with its accepted message');
  return previous;
}
export function pending_agent_messages(
  sql: ReadSql,
  recipient: Principal,
): readonly AcceptedAgentMessage[] {
  return sql
    .all<{ record_json: string }>(
      'SELECT record_json FROM agent_message_acceptances WHERE recipient_kind=? AND recipient_id=? AND pending=1 ORDER BY recipient_order',
      [recipient.kind, recipient.id],
    )
    .map((row) => decode_record(row.record_json));
}
export function retained_agent_message_files(
  sql: ReadSql,
  submission_id: string,
): readonly RetainedMessageFile[] {
  return sql
    .all<{ manifest_json: string; bytes: Buffer }>(
      'SELECT manifest_json,bytes FROM agent_message_files WHERE submission_id=? ORDER BY file_index',
      [submission_id],
    )
    .map((row) => ({
      manifest: decode_value(JSON.parse(row.manifest_json), 'FileManifest'),
      bytes: Buffer.from(row.bytes),
    }));
}
function verify_file(file: RetainedMessageFile): void {
  if (
    BigInt(file.bytes.length) !== BigInt(file.manifest.byte_count) ||
    createHash('sha256').update(file.bytes).digest('hex') !== file.manifest.sha256
  )
    throw new PlannerError('retry_later', 'Message file bytes no longer match the verified file');
}
export async function retain_published_message_files(
  published: PublishedFiles | null,
): Promise<readonly RetainedMessageFile[]> {
  const files: RetainedMessageFile[] = [];
  for (const file of published?.files ?? []) {
    const descriptor = await regular_descriptor({
      parent: file.parent,
      leaf: file.leaf,
      path: file.target,
    });
    try {
      const retained = { manifest: file.manifest, bytes: await descriptor.readFile() };
      verify_file(retained);
      files.push(retained);
    } finally {
      await descriptor.close();
    }
  }
  return files;
}
/** Caller also advances the sender's inbox position in this same transaction. */
export function write_agent_message_acceptance(
  tx: TransactionSql,
  message: Omit<AcceptedAgentMessage, 'recipient_order'>,
  files: readonly RetainedMessageFile[],
  delivery_order?: number,
): AcceptedAgentMessage {
  const prior = replay_agent_message(tx, message.envelope, message.machine_id, message.source);
  if (prior !== null) return prior;
  if (
    files.length !== (message.envelope.file_dependencies ?? []).length ||
    files.some(
      (file, index) =>
        !isDeepStrictEqual(
          as_object(file.manifest, 'FileManifest'),
          as_object(message.envelope.file_dependencies![index]!, 'FileManifest'),
        ),
    )
  )
    throw new PlannerError('retry_later', 'Message files are not retained');
  for (const file of files) verify_file(file);
  const retained_bytes = files.reduce((sum, file) => sum + file.bytes.length, 0);
  const recipient_order =
    delivery_order ??
    tx.get<{ next: number }>(
      'SELECT COALESCE(MAX(recipient_order),0)+1 AS next FROM agent_message_acceptances WHERE recipient_kind=? AND recipient_id=?',
      [message.recipient.kind, message.recipient.id],
    )!.next;
  const accepted = { ...message, recipient_order };
  const record_json = JSON.stringify({
    ...accepted,
    envelope: as_object(accepted.envelope, 'QueuedEnvelope'),
    receipt: as_object(accepted.receipt, 'AcceptanceReceipt'),
  });
  if (message.pending) {
    const pending = tx.get<{ count: number; bytes: number }>(
      'SELECT COUNT(*) AS count,COALESCE(SUM(retained_bytes+length(CAST(record_json AS BLOB))),0) AS bytes FROM agent_message_acceptances WHERE recipient_kind=? AND recipient_id=? AND pending=1',
      [message.recipient.kind, message.recipient.id],
    )!;
    if (
      pending.count >= MAX_PENDING_AGENT_MESSAGES ||
      pending.bytes + retained_bytes + Buffer.byteLength(record_json, 'utf8') >
        MAX_PENDING_AGENT_MESSAGE_BYTES
    )
      throw new PlannerError(
        'validation',
        'Home cannot retain more pending messages for this recipient',
      );
  }
  tx.run('INSERT INTO agent_message_acceptances VALUES(?,?,?,?,?,?,?,?,?,?)', [
    message.submission_id,
    message.machine_id,
    message.sender_id,
    message.source_conversation_id,
    message.recipient.kind,
    message.recipient.id,
    recipient_order,
    Number(message.pending),
    retained_bytes,
    record_json,
  ]);
  files.forEach((file, index) =>
    tx.run('INSERT INTO agent_message_files VALUES(?,?,?,?)', [
      message.submission_id,
      index,
      JSON.stringify(as_object(file.manifest, 'FileManifest')),
      file.bytes,
    ]),
  );
  return accepted;
}
