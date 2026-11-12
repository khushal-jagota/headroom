/** Secret-bearing values stay inside the host. Only CredentialMetadata is a public DTO. */
export type CredentialScheme = 'local';
export type CredentialAuthKind = 'bearer' | 'header' | 'basic';
export interface CredentialReference {
  scheme: CredentialScheme;
  name: string;
}
export interface CredentialMetadata {
  id: string;
  reference: CredentialReference;
  auth_kind: CredentialAuthKind;
  revision: number;
  created_at: number;
  updated_at: number;
}
export type CredentialSecret =
  | { kind: 'bearer'; token: string }
  | { kind: 'header'; header_name: string; value: string }
  | { kind: 'basic'; username: string; password: string };
export interface EncryptedCredential {
  nonce: Buffer;
  ciphertext: Buffer;
}
export interface CredentialEncryptionContext {
  version: number;
  associated_data: Buffer;
}
export interface ResolvedCredential {
  metadata: CredentialMetadata;
  secret: CredentialSecret;
}
export interface CredentialWrite {
  reference: CredentialReference;
  auth_kind: CredentialAuthKind;
  secret: CredentialSecret;
}
export interface CredentialCipher {
  encrypt(secret: CredentialSecret, associated_data: Buffer): EncryptedCredential;
  decrypt(encrypted: EncryptedCredential, associated_data: Buffer): CredentialSecret;
}
export interface CredentialResolver {
  resolve(reference: CredentialReference): ResolvedCredential;
}
