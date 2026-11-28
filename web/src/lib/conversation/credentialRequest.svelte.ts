import type { CredentialRequestRowBody } from './view/contracts';

/** A pane owns these drafts only in memory. Lens changes can remount its rows. */
export class CredentialRequestDraft {
  name = $state('');
  secret = $state('');
  revealed = $state(false);
  helpOpen = $state(false);
  busy = $state(false);
  error = $state('');
  invalid = $state<'name' | 'secret' | null>(null);
  saveFailed = $state(false);
  stale = $state(false);
  reviewed = $state(false);
  local = $state<CredentialRequestRowBody | null>(null);
  /** A fields request's values by field name. A file field holds the file's text. */
  fields = $state<Record<string, string>>({});
  /** The chosen file's name for each file field, shown in place of its text. */
  files = $state<Record<string, string>>({});
  constructor(name: string) { this.name = name; }
  /** Forget every entered secret value. */
  clearSecret(): void { this.secret = ''; this.fields = {}; this.files = {}; this.revealed = false; }
}

export type CredentialRequestDrafts = Map<string, CredentialRequestDraft>;
export const CREDENTIAL_REQUEST_DRAFTS = Symbol('credential-request-drafts');
export const CREDENTIAL_REQUEST_READ_ONLY = Symbol('credential-request-read-only');
