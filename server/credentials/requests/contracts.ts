/** Public request metadata reuses the generated Python contract. No secret enters it. */
import type { CredentialRequestRowBody } from '../../../web/src/lib/conversation/view/contracts.ts';
export type {
  CredentialRequestRowBody,
  RequestReplacement,
  RequestCredential,
  RequestDelivery,
  RequestDeliveryStatus,
} from '../../../web/src/lib/conversation/view/contracts.ts';
export type CreateCredentialRequestBody = Pick<
  CredentialRequestRowBody,
  | 'request_id'
  | 'name'
  | 'provider'
  | 'use'
  | 'auth_kind'
  | 'header_name'
  | 'help'
  | 'help_url'
  | 'replacement'
>;
