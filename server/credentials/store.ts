import { createCipheriv, createDecipheriv, randomBytes } from 'node:crypto';
import {
  existsSync,
  mkdirSync,
  openSync,
  closeSync,
  writeFileSync,
  fsyncSync,
  readSync,
} from 'node:fs';
import { dirname } from 'node:path';
import type { ReadSql } from '../core/database/contracts.ts';
import { KEY_BYTES, NONCE_BYTES } from './configuration.ts';
import type {
  CredentialCipher,
  CredentialSecret,
  EncryptedCredential,
  CredentialReference,
  ResolvedCredential,
  CredentialResolver,
} from './contracts.ts';
import { decode_secret, encode_secret, invalid, validate_secret } from './logic/validation.ts';
import * as data from './data.ts';

function load_key(path: string, has_records: boolean): Buffer {
  if (!existsSync(path)) {
    if (has_records) throw invalid('credential encryption key is unavailable');
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    let descriptor: number | undefined;
    try {
      descriptor = openSync(path, 'wx', 0o600);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error;
    }
    if (descriptor !== undefined) {
      try {
        writeFileSync(descriptor, randomBytes(KEY_BYTES));
        fsyncSync(descriptor);
      } finally {
        closeSync(descriptor);
      }
    }
  }
  const descriptor = openSync(path, 'r');
  const key = Buffer.alloc(KEY_BYTES + 1);
  let length: number;
  try {
    length = readSync(descriptor, key, 0, key.length, 0);
  } finally {
    closeSync(descriptor);
  }
  if (length !== KEY_BYTES) throw invalid('credential encryption key is unusable');
  return key.subarray(0, KEY_BYTES);
}
export class FileCredentialCipher implements CredentialCipher {
  readonly #key: Buffer;
  constructor(key_path: string, has_records: boolean) {
    try {
      this.#key = load_key(key_path, has_records);
    } catch (error) {
      if (
        error instanceof Error &&
        'code' in error &&
        (error as { code: string }).code !== 'validation'
      )
        throw invalid('credential encryption key is unavailable');
      throw error;
    }
  }
  encrypt(secret: CredentialSecret, associated_data: Buffer): EncryptedCredential {
    const nonce = randomBytes(NONCE_BYTES);
    const cipher = createCipheriv('aes-256-gcm', this.#key, nonce);
    cipher.setAAD(associated_data);
    const ciphertext = Buffer.concat([
      cipher.update(encode_secret(secret)),
      cipher.final(),
      cipher.getAuthTag(),
    ]);
    return { nonce, ciphertext };
  }
  decrypt(encrypted: EncryptedCredential, associated_data: Buffer): CredentialSecret {
    try {
      const decipher = createDecipheriv('aes-256-gcm', this.#key, encrypted.nonce);
      decipher.setAAD(associated_data);
      decipher.setAuthTag(encrypted.ciphertext.subarray(-16));
      return decode_secret(
        Buffer.concat([decipher.update(encrypted.ciphertext.subarray(0, -16)), decipher.final()]),
      );
    } catch {
      throw invalid('credential could not be decrypted');
    }
  }
}
/** Resolve within the caller's read/transaction scope. Retain no SQL capability beyond it. */
export class DatabaseCredentialResolver implements CredentialResolver {
  readonly #sql: ReadSql;
  readonly #cipher: CredentialCipher;
  constructor(sql: ReadSql, cipher: CredentialCipher) {
    this.#sql = sql;
    this.#cipher = cipher;
  }
  resolve(reference: CredentialReference): ResolvedCredential {
    const metadata = data.find_metadata(this.#sql, reference);
    if (!metadata) throw invalid('credential is unavailable');
    const secret = this.#cipher.decrypt(
      data.read_encrypted(this.#sql, metadata.id),
      data.read_encryption_context(this.#sql, metadata.id).associated_data,
    );
    validate_secret(secret, metadata.auth_kind);
    return { metadata, secret };
  }
}
