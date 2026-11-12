import type { Principal } from '../core/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import { newId } from '../core/ids.ts';
import { MAX_NAME_LENGTH } from './configuration.ts';
import type {
  CredentialMetadata,
  CredentialReference,
  CredentialSecret,
  CredentialWrite,
} from './contracts.ts';
import * as data from './data.ts';
import {
  associated_data,
  invalid,
  text,
  validate_secret,
  validate_write,
} from './logic/validation.ts';
import { FileCredentialCipher } from './store.ts';

export function require_plaintext_owner(principal: Principal): void {
  if (principal.kind !== 'owner')
    throw new PlannerError('agent_forbidden', 'only the owner can submit credential secrets');
}
function check_revision(metadata: CredentialMetadata, expected: number): void {
  if (!Number.isInteger(expected) || metadata.revision !== expected)
    throw new PlannerError('stale_claim', 'credential changed');
}
function require_unused_name(
  tx: TransactionSql,
  reference: CredentialReference,
  except_id = '',
): void {
  if (
    tx.get('SELECT 1 FROM credentials WHERE scheme=? AND name=? AND id!=? LIMIT 1', [
      reference.scheme,
      reference.name,
      except_id,
    ])
  )
    throw invalid('credential reference already exists');
}
export interface CredentialWriteOptions {
  principal: Principal;
  now: number;
  key_path: string;
}
/** Caller supplies the write-door transaction; this function never commits or saves. */
export function create_credential_in_transaction(
  tx: TransactionSql,
  write: CredentialWrite,
  options: CredentialWriteOptions,
): CredentialMetadata {
  require_plaintext_owner(options.principal);
  validate_write(write);
  require_unused_name(tx, write.reference);
  const metadata: CredentialMetadata = {
    id: newId('credential'),
    reference: write.reference,
    auth_kind: write.auth_kind,
    revision: 1,
    created_at: options.now,
    updated_at: options.now,
  };
  const cipher = new FileCredentialCipher(options.key_path, data.has_records(tx));
  const existing = tx.get<{ id: string }>('SELECT id FROM credentials LIMIT 1');
  if (existing) {
    cipher.decrypt(
      data.read_encrypted(tx, existing.id),
      data.read_encryption_context(tx, existing.id).associated_data,
    );
  }
  const context = associated_data(metadata);
  const encrypted = cipher.encrypt(write.secret, context);
  tx.run(
    `INSERT INTO credentials(
    id,scheme,name,auth_kind,encryption_version,encryption_context,
    nonce,ciphertext,revision,created_at,updated_at
  ) VALUES(?,?,?,?,2,?,?,?,?,?,?)`,
    [
      metadata.id,
      metadata.reference.scheme,
      metadata.reference.name,
      metadata.auth_kind,
      context,
      encrypted.nonce,
      encrypted.ciphertext,
      1,
      options.now,
      options.now,
    ],
  );
  return metadata;
}
export function replace_credential_in_transaction(
  tx: TransactionSql,
  id: string,
  secret: CredentialSecret,
  expected_revision: number,
  options: CredentialWriteOptions,
): CredentialMetadata {
  require_plaintext_owner(options.principal);
  const current = data.read_metadata(tx, id);
  check_revision(current, expected_revision);
  validate_secret(secret, current.auth_kind);
  const cipher = new FileCredentialCipher(options.key_path, true);
  const context = data.read_encryption_context(tx, id);
  cipher.decrypt(data.read_encrypted(tx, id), context.associated_data);
  const metadata = { ...current, revision: current.revision + 1, updated_at: options.now };
  const encrypted = cipher.encrypt(secret, context.associated_data);
  tx.run(
    `UPDATE credentials
    SET nonce=?,ciphertext=?,revision=?,updated_at=? WHERE id=?`,
    [encrypted.nonce, encrypted.ciphertext, metadata.revision, options.now, id],
  );
  return metadata;
}
export function rename_credential(
  tx: TransactionSql,
  id: string,
  name: string,
  expected_revision: number,
  options: CredentialWriteOptions,
): CredentialMetadata {
  require_plaintext_owner(options.principal);
  text(name, MAX_NAME_LENGTH);
  const current = data.read_metadata(tx, id);
  check_revision(current, expected_revision);
  const reference = { ...current.reference, name };
  require_unused_name(tx, reference, id);
  const cipher = new FileCredentialCipher(options.key_path, true);
  const secret = cipher.decrypt(
    data.read_encrypted(tx, id),
    data.read_encryption_context(tx, id).associated_data,
  );
  const metadata = {
    ...current,
    reference,
    revision: current.revision + 1,
    updated_at: options.now,
  };
  const context = associated_data(metadata);
  const encrypted = cipher.encrypt(secret, context);
  tx.run(
    `UPDATE credentials
    SET name=?,encryption_version=2,encryption_context=?,nonce=?,ciphertext=?,revision=?,updated_at=?
    WHERE id=?`,
    [name, context, encrypted.nonce, encrypted.ciphertext, metadata.revision, options.now, id],
  );
  return metadata;
}
/** DELETE authority belongs to shared HTTP admission, as in Python. */
export function delete_credential(tx: TransactionSql, id: string): void {
  data.read_metadata(tx, id);
  tx.run('DELETE FROM credentials WHERE id=?', [id]);
}
