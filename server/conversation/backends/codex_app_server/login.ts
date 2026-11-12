/** Read the saved Codex login without a refresh, network call, or retained secret. */
import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import {
  numeric_kind,
  numeric_value,
  parse_numeric_json,
} from '../../../core/http/numeric_json.ts';
import type {
  CodexLoginGeneration,
  CodexLoginRejectionMemory,
  StoredCodexLogin as StoredCodexLoginContract,
} from '../../snapshot/provider_contracts.ts';

export type {
  CodexLoginGeneration,
  CodexLoginRejectionMemory,
} from '../../snapshot/provider_contracts.ts';
export const LOGIN_FILE_NAME = 'auth.json';

export function codex_home(
  environment: Readonly<Record<string, string | undefined>> = process.env,
): string {
  // Python deliberately uses the process HOME, not a HOME from the supplied mapping.
  return environment.CODEX_HOME || join(homedir(), '.codex');
}

export class StoredCodexLogin implements StoredCodexLoginContract {
  readonly present: boolean;
  readonly readable: boolean;
  readonly account_label: string | null;
  readonly access_token_expires_at: Date | null;
  readonly generation: CodexLoginGeneration | null;

  constructor(options: {
    present: boolean;
    readable: boolean;
    account_label?: string | null;
    access_token_expires_at?: Date | null;
    generation?: CodexLoginGeneration | null;
  }) {
    this.present = options.present;
    this.readable = options.readable;
    this.account_label = options.account_label ?? null;
    this.access_token_expires_at = options.access_token_expires_at ?? null;
    this.generation = options.generation ?? null;
  }

  access_token_has_expired(options: { now: Date }): boolean {
    return this.access_token_expires_at !== null && this.access_token_expires_at <= options.now;
  }
}

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

function missing(error: unknown): boolean {
  return error instanceof Error && 'code' in error && error.code === 'ENOENT';
}

function text(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const stripped = value.replace(
    /^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu,
    '',
  );
  return stripped || null;
}

function read_document(home: string): unknown {
  const bytes = readFileSync(join(home, LOGIN_FILE_NAME));
  return JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes));
}

export function read_stored_codex_login(home?: string | null): StoredCodexLogin {
  let document: unknown;
  try {
    document = read_document(home || codex_home());
  } catch (error) {
    return new StoredCodexLogin({ present: !missing(error), readable: false });
  }
  if (!object(document)) return new StoredCodexLogin({ present: true, readable: false });
  const tokens = object(document.tokens) ? document.tokens : {};
  const account_id = text(tokens.account_id);
  const last_refresh = text(document.last_refresh);
  const claims = token_claims(tokens.id_token);
  return new StoredCodexLogin({
    present: true,
    readable: true,
    account_label: claims === null ? null : text(claims.email),
    access_token_expires_at: token_expiry(tokens.access_token),
    generation: account_id !== null && last_refresh !== null ? { account_id, last_refresh } : null,
  });
}

function token_claims(token: unknown): Record<string, unknown> | null {
  if (typeof token !== 'string') return null;
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    const bytes = Buffer.from(parts[1], 'base64url');
    const decoded: unknown = parse_numeric_json(
      new TextDecoder('utf-8', { fatal: true }).decode(bytes),
    );
    return object(decoded) ? decoded : null;
  } catch {
    return null;
  }
}

function token_expiry(token: unknown): Date | null {
  const claims = token_claims(token);
  if (claims === null) return null;
  // Python bool is an int. A lexical floating-point token, including 1.0, is not.
  const value = claims.exp;
  const seconds =
    typeof value === 'boolean'
      ? Number(value)
      : numeric_kind(value) === 'integer'
        ? numeric_value(value)
        : undefined;
  if (seconds === undefined) return null;
  const result = new Date(seconds * 1000);
  return Number.isFinite(result.getTime()) &&
    result.getUTCFullYear() >= 1 &&
    result.getUTCFullYear() <= 9999
    ? result
    : null;
}

function credentials(home: string): readonly string[] | null {
  let document: unknown;
  try {
    document = read_document(home);
  } catch (error) {
    return missing(error) ? [] : null;
  }
  if (!object(document)) return null;
  const tokens = object(document.tokens) ? document.tokens : {};
  return [tokens.refresh_token, document.OPENAI_API_KEY].filter(
    (value): value is string => typeof value === 'string' && value.length !== 0,
  );
}

export function logins_hold_the_same_credential(one: string, other: string): boolean | null {
  const first = credentials(one);
  const second = credentials(other);
  if (first === null || second === null) return null;
  if (first.length === 0 || second.length === 0) return false;
  const key = randomBytes(32);
  const digest = (secret: string): Buffer => createHmac('sha256', key).update(secret).digest();
  const marked = first.map(digest);
  return second.some((secret) => {
    const candidate = digest(secret);
    return marked.some((held) => timingSafeEqual(held, candidate));
  });
}

function same_generation(one: CodexLoginGeneration | null, other: CodexLoginGeneration): boolean {
  return (
    one !== null && one.account_id === other.account_id && one.last_refresh === other.last_refresh
  );
}

export class RememberedCodexLoginRejection implements CodexLoginRejectionMemory {
  private rejected: CodexLoginGeneration | null = null;

  remember(generation: CodexLoginGeneration | null): void {
    if (generation !== null) this.rejected = generation;
  }

  forget(generation: CodexLoginGeneration | null): void {
    if (generation !== null && same_generation(this.rejected, generation)) this.rejected = null;
  }

  applies_to(generation: CodexLoginGeneration | null): boolean {
    return generation !== null && same_generation(this.rejected, generation);
  }
}
