import type { CredentialAuthKind, SecretBody } from './wire';

/** This mutable object lives only inside the mounted password form. */
export type SecretFields = { token: string; headerName: string; value: string; username: string; password: string };
export function emptySecretFields(): SecretFields {
  return { token: '', headerName: '', value: '', username: '', password: '' };
}
export function clearSecretFields(fields: SecretFields): void {
  fields.token = fields.headerName = fields.value = fields.username = fields.password = '';
}
export function secretBody(kind: CredentialAuthKind, fields: SecretFields): SecretBody {
  if (kind === 'bearer') return { token: fields.token };
  if (kind === 'header') return { header_name: fields.headerName, value: fields.value };
  return { username: fields.username, password: fields.password };
}
