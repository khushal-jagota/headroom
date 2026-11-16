import type { ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { pageFacts } from '../list_reads/logic/index.ts';
import { MAX_LIST_LIMIT } from './configuration.ts';
import type {
  CredentialAuthKind,
  CredentialMetadata,
  CredentialReference,
  CredentialEncryptionContext,
  EncryptedCredential,
} from './contracts.ts';
import { invalid, validate_encryption_context } from './logic/validation.ts';

const PUBLIC_COLUMNS = 'id,scheme,name,auth_kind,revision,created_at,updated_at';
interface MetadataRow {
  id: string;
  scheme: 'local';
  name: string;
  auth_kind: CredentialAuthKind;
  revision: number;
  created_at: number;
  updated_at: number;
}
function metadata(row: MetadataRow): CredentialMetadata {
  return {
    id: row.id,
    reference: { scheme: row.scheme, name: row.name },
    auth_kind: row.auth_kind,
    revision: row.revision,
    created_at: row.created_at,
    updated_at: row.updated_at,
  };
}
export function read_metadata(sql: ReadSql, id: string): CredentialMetadata {
  const row = sql.get<MetadataRow>(`SELECT ${PUBLIC_COLUMNS} FROM credentials WHERE id=?`, [id]);
  if (!row) throw new PlannerError('not_found', 'credential not found');
  return metadata(row);
}
export function list_metadata(sql: ReadSql, limit: number, offset: number) {
  if (
    !Number.isInteger(limit) ||
    limit < 1 ||
    limit > MAX_LIST_LIMIT ||
    !Number.isInteger(offset) ||
    offset < 0
  )
    throw invalid('invalid bounded credential list');
  const count = sql.get<{ count: number }>('SELECT COUNT(*) AS count FROM credentials')!.count;
  const credentials = sql
    .all<MetadataRow>(`SELECT ${PUBLIC_COLUMNS} FROM credentials ORDER BY id LIMIT ? OFFSET ?`, [
      limit,
      offset,
    ])
    .map(metadata);
  return { credentials, page: pageFacts(count, credentials.length, { limit, offset }) };
}
export function read_encrypted(sql: ReadSql, id: string): EncryptedCredential {
  const row = sql.get<EncryptedCredential>('SELECT nonce,ciphertext FROM credentials WHERE id=?', [
    id,
  ]);
  if (!row) throw new PlannerError('not_found', 'credential not found');
  return { nonce: Buffer.from(row.nonce), ciphertext: Buffer.from(row.ciphertext) };
}
export function has_records(sql: ReadSql): boolean {
  return sql.get('SELECT 1 FROM credentials LIMIT 1') !== undefined;
}
export function read_encryption_context(sql: ReadSql, id: string): CredentialEncryptionContext {
  const row = sql.get<{ encryption_version: number; encryption_context: Buffer }>(
    'SELECT encryption_version,encryption_context FROM credentials WHERE id=?',
    [id],
  );
  if (!row) throw new PlannerError('not_found', 'credential not found');
  const context = {
    version: row.encryption_version,
    associated_data: Buffer.from(row.encryption_context),
  };
  validate_encryption_context(read_metadata(sql, id), context);
  return context;
}
export function find_metadata(
  sql: ReadSql,
  reference: CredentialReference,
): CredentialMetadata | null {
  const rows = sql.all<MetadataRow>(
    `SELECT ${PUBLIC_COLUMNS} FROM credentials WHERE scheme=? AND name=? ORDER BY id`,
    [reference.scheme, reference.name],
  );
  if (rows.length > 1)
    throw new PlannerError('validation', 'credential reference is ambiguous', {
      candidates: rows.map(metadata),
    });
  return rows.length ? metadata(rows[0]!) : null;
}
