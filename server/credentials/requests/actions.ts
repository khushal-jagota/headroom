/** One canonical request transition, with secret writes in the caller's transaction. */
import { randomUUID } from 'node:crypto';
import type { Principal, RequestContext } from '../../core/contracts.ts';
import { PlannerError } from '../../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { numeric_kind, numeric_value } from '../../core/http/numeric_json.ts';
import type { EventMutation } from '../../conversation/record/contracts.ts';
import type { CredentialMetadata, CredentialSecret } from '../contracts.ts';
import * as credential_actions from '../actions.ts';
import * as credential_data from '../data.ts';
import { invalid, text } from '../logic/validation.ts';
import { DatabaseCredentialResolver, FileCredentialCipher } from '../store.ts';
import { read_manager } from '../../managers/data.ts';
import { read_ticket } from '../../tickets/data/index.ts';
import { CHIEF_SETTINGS_KEY } from '../../worker_settings/service.ts';
import * as data from './data.ts';
import type { CredentialRequestRowBody, RequestCredential } from './contracts.ts';
import { strip, validate_request } from './logic/validation.ts';

type Result = EventMutation<readonly [CredentialRequestRowBody, string | null]>;
interface OwnerOptions {
  principal: Principal;
  now: number;
}
export function origin_conversation(sql: ReadSql, ctx: RequestContext): string {
  const principal = ctx.principal;
  const origin = ctx.conversationId;
  if (principal.kind === 'owner' || !origin)
    throw new PlannerError(
      'agent_forbidden',
      'credential requests require the employee conversation identity',
    );
  const agent_conversation = (key: string): string | null =>
    sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM agents WHERE agent_key=?',
      [key],
    )?.conversation_id ?? null;
  let current: string | null;
  if (principal.kind === 'ticket') current = read_ticket(sql, principal.id).conversation_id;
  else if (principal.kind === 'chief') current = agent_conversation(CHIEF_SETTINGS_KEY);
  else if (principal.kind === 'manager')
    current = agent_conversation(read_manager(sql, principal.id).item.manager_agent_key);
  else current = null;
  if (current !== origin)
    throw new PlannerError('agent_forbidden', 'the requesting conversation is no longer current');
  const row = sql.get<{ identity_environment_variables: string }>(
    'SELECT identity_environment_variables FROM conversations WHERE conversation_id=?',
    [origin],
  );
  if (row === undefined)
    throw new PlannerError('agent_forbidden', 'the requesting conversation is absent');
  const parsed: unknown = JSON.parse(row.identity_environment_variables);
  const identity = (Array.isArray(parsed) ? Object.fromEntries(parsed) : parsed) as Record<
    string,
    unknown
  >;
  const actor = identity.PLAN_ACTOR;
  const matches =
    (principal.kind === 'ticket' &&
      actor === 'worker' &&
      identity.PLAN_TICKET_ID === principal.id) ||
    (principal.kind === 'chief' && actor === 'chief') ||
    (principal.kind === 'manager' &&
      actor === 'manager' &&
      identity.PLAN_MANAGER_ID === principal.id);
  const conversation_id =
    'PLAN_CONVERSATION_ID' in identity ? identity.PLAN_CONVERSATION_ID : origin;
  if (!matches || conversation_id !== origin)
    throw new PlannerError('agent_forbidden', 'the requesting conversation identity differs');
  return origin;
}
export function public_credential(metadata: CredentialMetadata): RequestCredential {
  if (metadata.auth_kind !== 'bearer' && metadata.auth_kind !== 'header')
    throw invalid('credential authentication type differs');
  return {
    id: metadata.id,
    reference: { scheme: metadata.reference.scheme, name: metadata.reference.name },
    auth_kind: metadata.auth_kind,
    revision: metadata.revision,
  };
}
function stale(metadata: CredentialMetadata): PlannerError {
  return new PlannerError('stale_claim', 'credential changed', { current_credential: metadata });
}
function attempt(tx: TransactionSql, body: CredentialRequestRowBody, now: number): string {
  const attempt_id = randomUUID();
  const message = data.encoded({
    credential_request_id: body.request_id,
    outcome: body.state,
    credential_reference: body.credential?.reference ?? null,
  });
  const prompt =
    'API key request result: ' +
    message +
    '. Continue the authorized work with this outcome. ' +
    'A saved value does not prove that a service accepts it.';
  tx.run(
    `
    INSERT INTO credential_request_delivery_attempts
      (attempt_id,request_id,sender_message_id,prompt,status,created_at)
    VALUES(?,?,?,?,'intent',?)
  `,
    [attempt_id, body.request_id, 'credential-request:' + attempt_id, prompt, now],
  );
  body.delivery = { attempt_id, status: 'pending', refusal_reason: null };
  return attempt_id;
}
export function create(
  tx: TransactionSql,
  raw: unknown,
  options: {
    ctx: RequestContext;
    now: number;
    key_path: string;
  },
): Result {
  const origin = origin_conversation(tx, options.ctx);
  const requested = validate_request(raw);
  const request_id = requested.request_id;
  const previous = tx.get<data.CredentialRequestRow>(
    'SELECT * FROM credential_requests WHERE request_id=?',
    [request_id],
  );
  if (previous !== undefined) {
    if (
      previous.conversation_id !== origin ||
      previous.requester_kind !== options.ctx.principal.kind ||
      previous.requester_id !== options.ctx.principal.id ||
      previous.request_json !== data.encoded(requested)
    )
      throw invalid('request_id already names a different credential request');
    return { value: [data.read(tx, request_id), null], payloads: [] };
  }
  const replacement = requested.replacement;
  let credential: RequestCredential | null = null;
  if (replacement !== null) {
    const metadata = credential_data.read_metadata(tx, replacement.credential_id);
    if (metadata.revision !== replacement.revision) throw stale(metadata);
    if (requested.name !== metadata.reference.name || replacement.name !== metadata.reference.name)
      throw invalid('a replacement keeps the current credential name');
    if (metadata.auth_kind !== requested.auth_kind)
      throw invalid('credential authentication type differs');
  } else {
    const existing = credential_data.find_metadata(tx, { scheme: 'local', name: requested.name });
    if (existing !== null) {
      if (existing.auth_kind !== requested.auth_kind)
        throw invalid('credential authentication type differs');
      if (requested.auth_kind === 'header') {
        const resolver = new DatabaseCredentialResolver(
          tx,
          new FileCredentialCipher(options.key_path, true),
        );
        const saved = resolver.resolve(existing.reference);
        if (saved.secret.kind !== 'header' || saved.secret.header_name !== requested.header_name)
          throw invalid('saved credential header differs. Request an explicit replacement');
      }
      credential = public_credential(existing);
    }
  }
  const body: CredentialRequestRowBody = {
    kind: 'credential_request',
    ...requested,
    state: credential !== null ? 'reused' : 'pending',
    credential,
    delivery: null,
  };
  tx.run(
    `
    INSERT INTO credential_requests
      (request_id,conversation_id,requester_kind,requester_id,request_json,body,created_at,updated_at)
    VALUES(?,?,?,?,?,?,?,?)
  `,
    [
      request_id,
      origin,
      options.ctx.principal.kind,
      options.ctx.principal.id,
      data.encoded(requested),
      data.encoded(body),
      options.now,
      options.now,
    ],
  );
  const delivery = credential !== null ? attempt(tx, body, options.now) : null;
  return { value: [body, delivery], payloads: [data.update(tx, body, options.now)] };
}
export function save(
  tx: TransactionSql,
  request_id: string,
  options: OwnerOptions & {
    name: unknown;
    value: unknown;
    expected_revision: unknown;
    key_path: string;
  },
): Result {
  credential_actions.require_plaintext_owner(options.principal);
  const body = data.read(tx, request_id);
  if (body.state !== 'pending') return { value: [body, null], payloads: [] };
  const name = strip(text(options.name, 200));
  const value = text(options.value);
  const replacement = body.replacement;
  const expected_revision = numeric_value(options.expected_revision);
  if (replacement !== null) {
    const current = credential_data.read_metadata(tx, replacement.credential_id);
    if (
      numeric_kind(options.expected_revision) !== 'integer' ||
      expected_revision !== replacement.revision ||
      current.revision !== expected_revision
    )
      throw stale(current);
    if (name !== replacement.name || current.reference.name !== replacement.name)
      throw invalid('a replacement keeps the current credential name');
  } else if (options.expected_revision !== null)
    throw invalid('new credentials do not have a replacement revision');
  const secret: CredentialSecret =
    body.auth_kind === 'bearer'
      ? { kind: 'bearer', token: value }
      : { kind: 'header', header_name: body.header_name || '', value };
  let metadata: CredentialMetadata;
  if (replacement === null) {
    metadata = credential_actions.create_credential_in_transaction(
      tx,
      {
        reference: { scheme: 'local', name },
        auth_kind: body.auth_kind,
        secret,
      },
      options,
    );
    body.state = 'saved';
  } else {
    metadata = credential_actions.replace_credential_in_transaction(
      tx,
      replacement.credential_id,
      secret,
      expected_revision!,
      options,
    );
    body.state = 'replaced';
  }
  body.name = metadata.reference.name;
  body.credential = public_credential(metadata);
  const delivery = attempt(tx, body, options.now);
  return { value: [body, delivery], payloads: [data.update(tx, body, options.now)] };
}
export function reject(tx: TransactionSql, request_id: string, options: OwnerOptions): Result {
  credential_actions.require_plaintext_owner(options.principal);
  const body = data.read(tx, request_id);
  if (body.state !== 'pending') return { value: [body, null], payloads: [] };
  body.state = 'rejected';
  const delivery = attempt(tx, body, options.now);
  return { value: [body, delivery], payloads: [data.update(tx, body, options.now)] };
}
export function review(
  tx: TransactionSql,
  request_id: string,
  options: OwnerOptions,
): EventMutation<CredentialRequestRowBody> {
  credential_actions.require_plaintext_owner(options.principal);
  const body = data.read(tx, request_id);
  const replacement = body.replacement;
  if (body.state !== 'pending' || replacement === null) return { value: body, payloads: [] };
  const metadata = credential_data.read_metadata(tx, replacement.credential_id);
  if (metadata.auth_kind !== body.auth_kind)
    throw invalid('credential authentication type differs');
  body.replacement = {
    credential_id: metadata.id,
    name: metadata.reference.name,
    revision: metadata.revision,
  };
  body.name = metadata.reference.name;
  return { value: body, payloads: [data.update(tx, body, options.now)] };
}
export function retry(tx: TransactionSql, request_id: string, options: OwnerOptions): Result {
  credential_actions.require_plaintext_owner(options.principal);
  const body = data.read(tx, request_id);
  if (body.delivery === null || body.delivery.status !== 'refused')
    return { value: [body, null], payloads: [] };
  const delivery = attempt(tx, body, options.now);
  return { value: [body, delivery], payloads: [data.update(tx, body, options.now)] };
}
