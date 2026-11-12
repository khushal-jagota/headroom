/** Outbox format 5, unchanged from the Python executor. */
export const DATABASE_NAME = 'outbox.sqlite3';
export { SPOOL_FORMAT_VERSION } from '../../wire.generated.ts';
export const FORMAT_REFUSAL = 'unsupported outbox format; preserve the spool for compatible Panels';
export const OUTBOX_SCHEMA = [
  'CREATE TABLE sender (singleton INTEGER PRIMARY KEY CHECK(singleton=1), sender_id TEXT NOT NULL)',
  `CREATE TABLE items (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL,
    child_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json BLOB NOT NULL,
    receipt_json BLOB
  )`,
  'CREATE INDEX waiting ON items(conversation_id,sequence) WHERE receipt_json IS NULL',
  `CREATE TABLE item_files (
    sequence INTEGER NOT NULL REFERENCES items(sequence) ON DELETE CASCADE,
    relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
    PRIMARY KEY(sequence,relative_path)
  ) WITHOUT ROWID`,
  'CREATE INDEX item_files_blob ON item_files(sha256)',
  `CREATE TABLE ticket_files (
    ticket_id TEXT NOT NULL, relative_path TEXT NOT NULL, byte_count INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL, sha256 TEXT NOT NULL,
    PRIMARY KEY(ticket_id,relative_path)
  ) WITHOUT ROWID`,
] as const;
