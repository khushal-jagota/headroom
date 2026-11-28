import { PlannerError } from '../../core/contracts.ts';
import {
  FIELD_NAME_PATTERN,
  MAX_FIELDS,
  MAX_FIELD_VALUE_LENGTH,
  MAX_NAME_LENGTH,
} from '../configuration.ts';
import type {
  CredentialAuthKind,
  CredentialEncryptionContext,
  CredentialMetadata,
  CredentialSecret,
  CredentialWrite,
} from '../contracts.ts';

export function invalid(
  message = 'invalid credential input',
  options?: ErrorOptions,
): PlannerError {
  return new PlannerError('validation', message, {}, options);
}
export function text(value: unknown, maximum?: number, allow_empty = false): string {
  if (
    typeof value !== 'string' ||
    (maximum !== undefined && Array.from(value).length > maximum) ||
    (!allow_empty && !value.trim())
  )
    throw invalid();
  return value;
}
/** Field names are a fixed, ordered list. A fields request and its save both use this rule. */
export function validate_field_names(names: readonly unknown[]): string[] {
  if (names.length < 1 || names.length > MAX_FIELDS) throw invalid('invalid credential fields');
  const valid = names.map((name) => {
    if (typeof name !== 'string' || !FIELD_NAME_PATTERN.test(name))
      throw invalid('invalid credential field name');
    return name;
  });
  if (new Set(valid).size !== valid.length) throw invalid('credential field names repeat');
  return valid;
}
export function validate_secret(secret: CredentialSecret, kind: CredentialAuthKind): void {
  if (secret.kind !== kind) throw invalid('credential authentication type differs');
  if (secret.kind === 'fields') {
    if (secret.fields === null || typeof secret.fields !== 'object' || Array.isArray(secret.fields))
      throw invalid();
    validate_field_names(Object.keys(secret.fields));
    for (const value of Object.values(secret.fields)) {
      // Values can span lines, as a PEM private key does. Other control characters are refused.
      if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(text(value, MAX_FIELD_VALUE_LENGTH)))
        throw invalid();
    }
  } else if (secret.kind === 'basic') {
    text(secret.username, undefined, true);
    text(secret.password, undefined, true);
    if (secret.username.includes(':')) throw invalid();
  } else {
    const value = text(secret.kind === 'bearer' ? secret.token : secret.value);
    if (/[\x00-\x1f\x7f]/.test(value)) throw invalid();
    if (
      secret.kind === 'header' &&
      (typeof secret.header_name !== 'string' ||
        !secret.header_name.length ||
        /[^!#$%&'*+.^_`|~0-9A-Za-z-]/.test(secret.header_name))
    ) {
      throw invalid();
    }
  }
}
export function validate_write(write: CredentialWrite): CredentialWrite {
  text(write.reference.name, MAX_NAME_LENGTH);
  if (write.reference.scheme !== 'local') throw invalid('unsupported credential resolver');
  validate_secret(write.secret, write.auth_kind);
  return write;
}
/** Python json.dumps(sort_keys=True, separators=(',', ':')) uses ensure_ascii=True. */
function canonical(value: Record<string, unknown>): Buffer {
  const sorted = Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b)));
  return Buffer.from(
    JSON.stringify(sorted).replace(
      /[\u007f-\uffff]/g,
      (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, '0')}`,
    ),
  );
}
export function associated_data(metadata: CredentialMetadata): Buffer {
  return canonical({
    credential_id: metadata.id,
    scheme: metadata.reference.scheme,
    name: metadata.reference.name,
    auth_kind: metadata.auth_kind,
  });
}
export function encode_secret(secret: CredentialSecret): Buffer {
  validate_secret(secret, secret.kind);
  return canonical(secret as unknown as Record<string, unknown>);
}
export function decode_secret(raw: Buffer): CredentialSecret {
  try {
    const body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(raw));
    let secret: CredentialSecret;
    if (body.kind === 'bearer') secret = { kind: 'bearer', token: body.token };
    else if (body.kind === 'header')
      secret = { kind: 'header', header_name: body.header_name, value: body.value };
    else if (body.kind === 'basic')
      secret = { kind: 'basic', username: body.username, password: body.password };
    else if (body.kind === 'fields') secret = { kind: 'fields', fields: body.fields };
    else throw invalid();
    validate_secret(secret, secret.kind);
    return secret;
  } catch {
    throw invalid('credential could not be decrypted');
  }
}
export function validate_encryption_context(
  metadata: CredentialMetadata,
  context: CredentialEncryptionContext,
): void {
  try {
    const body: Record<string, unknown> = JSON.parse(
      new TextDecoder('utf-8', { fatal: true }).decode(context.associated_data),
    );
    const required = ['scheme', 'name', 'auth_kind'];
    if (context.version === 1) required.push('project_id', 'account');
    else if (context.version === 2) required.push('credential_id');
    else throw invalid();
    if (
      body === null ||
      Array.isArray(body) ||
      Object.keys(body).sort().join() !== required.sort().join() ||
      Object.values(body).some((value) => typeof value !== 'string') ||
      body.scheme !== metadata.reference.scheme ||
      body.name !== metadata.reference.name ||
      body.auth_kind !== metadata.auth_kind ||
      (context.version === 2 && body.credential_id !== metadata.id)
    )
      throw invalid();
  } catch {
    throw invalid('credential could not be decrypted');
  }
}
