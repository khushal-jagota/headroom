/** Durable Review outcomes share the canonical Ticket transaction. */
import { PlannerError, type Principal } from '../core/contracts.ts';
import { requireAbove } from '../core/authority/service.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { is_record } from '../core/records.ts';
import {
  JsonNumber,
  parse_numeric_json,
  stringify_python_json,
} from '../core/http/numeric_json.ts';
import { StoredTicketIntegrityError } from './contracts.ts';
import type {
  DecisionCommand,
  DecisionHolder,
  DecisionOperationResponse,
  DecisionReceipt,
  DecisionTransition,
  Ticket,
} from './contracts.ts';
import * as data from './data/index.ts';
import { validated_sent_files, type SentPiece } from '../conversation/http.ts';
import { HttpError } from '../core/http/errors.ts';
import type { VideoUploadService } from '../conversation/video_uploads/service.ts';
import { completion_follows, consequences_follows } from './logic/machine.ts';
import { validate_revision_guidance, REVISION_GUIDANCE_MAX_CHARACTERS } from './logic/admission.ts';

const business_refusals = new Set([
  'validation',
  'scope_missing',
  'scope_invalid',
  'not_found',
  'already_running',
]);
const validation = (message: string) => new PlannerError('validation', message);
function identity(value: string, name: string): void {
  if (!value || value !== value.trim())
    throw validation(`${name} must be nonempty text without surrounding whitespace`);
}
function holder(value: unknown): DecisionHolder | null {
  if (value === null || value === undefined) return null;
  if (!is_record(value) || Object.keys(value).sort().join(',') !== 'id,kind')
    throw validation('next_holder must name kind and id');
  if (
    typeof value.kind !== 'string' ||
    !['owner', 'chief', 'manager'].includes(value.kind) ||
    typeof value.id !== 'string'
  )
    throw validation('next_holder must name owner, chief, or manager with a text id');
  if (!value.id || value.id !== value.id.trim())
    throw validation('principal id must be non-empty and must not contain outer whitespace');
  if ((value.kind === 'owner' || value.kind === 'chief') && value.id !== value.kind)
    throw validation(`${value.kind} principal id must be ${value.kind}`);
  return { kind: value.kind as DecisionHolder['kind'], id: value.id };
}
/** Pictures and files that travel with a decision, in the same pieces a conversation message uses. */
function attachments(value: unknown): void {
  if (!Array.isArray(value)) throw validation('attachments must be a list of pictures and files');
  const sent: SentPiece[] = value.map((piece: unknown) => {
    if (
      is_record(piece) &&
      (piece.piece === 'attachment_upload' || piece.piece === 'video_upload') &&
      typeof piece.upload_id === 'string' &&
      Object.keys(piece).every((key) => ['piece', 'upload_id'].includes(key))
    )
      return piece as unknown as SentPiece;
    if (
      !is_record(piece) ||
      (piece.piece !== 'image' && piece.piece !== 'file') ||
      typeof piece.data !== 'string' ||
      typeof piece.media_type !== 'string' ||
      (piece.piece === 'file'
        ? typeof piece.file_name !== 'string'
        : piece.file_name != null && typeof piece.file_name !== 'string')
    )
      throw validation('each attachment must be an image or file piece');
    return piece as unknown as SentPiece;
  });
  try {
    validated_sent_files(sent);
  } catch (error) {
    if (error instanceof HttpError) throw validation(error.message);
    throw error;
  }
}
export function parse_command(raw: Record<string, unknown>): DecisionCommand {
  const decision = raw.decision;
  if (decision !== 'approve' && decision !== 'send_back')
    throw validation('decision must be approve or send_back');
  const allowed = [
    'decision',
    'field',
    'expected_proposal_revision',
    'message',
    'attachments',
    ...(decision === 'approve' ? ['edited_body', 'next_ceiling', 'next_holder'] : []),
  ];
  if (Object.keys(raw).some((key) => !allowed.includes(key)))
    throw validation('unknown decision command fields');
  if (typeof raw.field !== 'string') throw validation('field must be text');
  identity(raw.field, 'field');
  const supplied = raw.expected_proposal_revision;
  const revision =
    supplied instanceof JsonNumber
      ? supplied.integer
      : typeof supplied === 'bigint'
        ? supplied
        : typeof supplied === 'number' && Number.isInteger(supplied)
          ? BigInt(supplied)
          : null;
  if (revision === null || revision <= 0n)
    throw validation('expected_proposal_revision must be a positive integer');
  for (const name of ['edited_body', 'next_ceiling', 'message'])
    if (Object.hasOwn(raw, name) && raw[name] !== null && typeof raw[name] !== 'string')
      throw validation(`${name} must be text or null`);
  if (typeof raw.message === 'string') validate_revision_guidance(raw.message);
  if (Object.hasOwn(raw, 'attachments')) attachments(raw.attachments);
  if (
    typeof raw.edited_body === 'string' &&
    Array.from(raw.edited_body).length > REVISION_GUIDANCE_MAX_CHARACTERS
  )
    throw validation(`edited_body must be at most ${REVISION_GUIDANCE_MAX_CHARACTERS} characters`);
  if (Object.hasOwn(raw, 'next_holder')) holder(raw.next_holder);
  return { ...raw, expected_proposal_revision: revision } as DecisionCommand;
}
const encoded = (value: unknown): string =>
  stringify_python_json(value, { sort_keys: true, compact: true, ensure_ascii: false });
const response = (receipt_json: string | null): DecisionOperationResponse => ({
  receipt_contract: 1,
  receipt:
    receipt_json === null
      ? null
      : (parse_numeric_json(receipt_json) as DecisionOperationResponse['receipt']),
});
function require_ticket(sql: ReadSql, ticket_id: string, actor: Principal): void {
  if (!sql.get('SELECT 1 FROM tickets WHERE id=?', [ticket_id]))
    throw new PlannerError('not_found', 'ticket not found', { ticket_id });
  requireAbove(sql, actor, { kind: 'ticket', id: ticket_id });
}
export function lookup(
  sql: ReadSql,
  ticket_id: string,
  operation_id: string,
  actor: Principal,
): DecisionOperationResponse {
  identity(operation_id, 'operation_id');
  require_ticket(sql, ticket_id, actor);
  const row = sql.get<{ receipt_json: string }>(
    'SELECT receipt_json FROM ticket_decision_operations WHERE operation_id=? AND ticket_id=? AND actor_kind=? AND actor_id=?',
    [operation_id, ticket_id, actor.kind, actor.id],
  );
  return response(row?.receipt_json ?? null);
}
function transition(
  tx: TransactionSql,
  ticket: Ticket,
  command: DecisionCommand,
  operation_id: string,
  actor: Principal,
  now: number,
): DecisionTransition {
  if (BigInt(command.expected_proposal_revision) !== BigInt(ticket.pending_proposal_revision))
    throw new PlannerError(
      'validation',
      'the visible proposal changed; read it again before deciding',
      { ticket_id: ticket.id },
    );
  let result: Ticket;
  if (command.decision === 'approve') {
    const next_holder = holder(command.next_holder);
    if (
      next_holder === null &&
      !completion_follows(ticket.stage, ticket.workflow) &&
      !consequences_follows(ticket.stage, ticket.workflow)
    )
      throw new PlannerError('scope_missing', 'approval requires next_holder', {
        missing: ['next_holder'],
      });
    result = data.accept_proposal(tx, ticket.id, command.field, {
      principal: actor,
      now,
      edited_body: command.edited_body ?? null,
      next_ceiling: command.next_ceiling ?? null,
      next_holder,
      expected_proposal_revision: command.expected_proposal_revision,
      message: command.message ?? null,
      decision_id: operation_id,
    });
  } else {
    if (ticket.pending_proposal?.field !== command.field)
      throw validation('proposal field is not the current gate');
    result = data.reject_proposal(tx, ticket.id, {
      principal: actor,
      now,
      message: command.message ?? null,
      expected_proposal_revision: command.expected_proposal_revision,
      decision_id: operation_id,
    });
  }
  return {
    stage: result.stage,
    ceiling: result.ceiling,
    ceiling_holder: result.ceiling_holder as DecisionHolder,
    ticket_status: result.ticket_status,
    pending_proposal_revision: result.pending_proposal_revision,
  };
}
export function apply(
  tx: TransactionSql,
  ticket_id: string,
  operation_id: string,
  raw: Record<string, unknown>,
  actor: Principal,
  now: number,
  uploads?: VideoUploadService,
): DecisionOperationResponse {
  identity(operation_id, 'operation_id');
  const command = parse_command(raw),
    command_json = encoded(command);
  require_ticket(tx, ticket_id, actor);
  const saved = tx.get<{
    ticket_id: string;
    actor_kind: string;
    actor_id: string;
    command_json: string;
    receipt_json: string;
  }>(
    'SELECT ticket_id,actor_kind,actor_id,command_json,receipt_json FROM ticket_decision_operations WHERE operation_id=?',
    [operation_id],
  );
  if (saved) {
    if (saved.actor_kind !== actor.kind || saved.actor_id !== actor.id)
      throw new PlannerError('agent_forbidden', 'decision operation identity is unavailable');
    if (saved.ticket_id !== ticket_id)
      throw validation('decision operation identity was reused for another Ticket');
    if (saved.command_json !== command_json)
      throw validation('decision operation identity was reused with different content');
    return response(saved.receipt_json);
  }
  const current = data.load_ticket_for_write(tx, ticket_id);
  const receipt: DecisionReceipt = {
    operation_id,
    ticket_id,
    decision: command.decision,
    field: command.field,
    expected_proposal_revision: command.expected_proposal_revision,
    outcome: 'applied',
    transition: null,
    error: null,
    created_at: now,
  };
  try {
    receipt.transition = tx.savepoint((scope) => {
      const ids = new Set<string>();
      for (const piece of command.attachments ?? []) {
        if (piece.piece !== 'attachment_upload' && piece.piece !== 'video_upload') continue;
        if (!uploads || actor.kind !== 'owner')
          throw validation('Uploaded decision attachments require owner upload admission.');
        const id = piece.upload_id as string;
        if (ids.has(id)) throw validation('An upload can appear only once in a decision.');
        ids.add(id);
        try {
          uploads.preflight(
            id,
            { kind: 'ticket', id: ticket_id, expected_conversation_id: current.conversation_id },
            'decision-upload:' + operation_id,
            scope,
          );
        } catch (error) {
          throw validation(error instanceof Error ? error.message : String(error));
        }
      }
      return transition(scope, current, command, operation_id, actor, now);
    });
  } catch (error) {
    if (
      !(error instanceof PlannerError) ||
      error instanceof StoredTicketIntegrityError ||
      !business_refusals.has(error.code)
    )
      throw error;
    receipt.outcome = 'refused';
    receipt.error = { code: error.code, message: error.message, detail: error.detail };
  }
  const receipt_json = encoded(receipt);
  tx.run(
    'INSERT INTO ticket_decision_operations (ticket_id,operation_id,actor_kind,actor_id,command_json,receipt_json,created_at) VALUES (?,?,?,?,?,?,?)',
    [ticket_id, operation_id, actor.kind, actor.id, command_json, receipt_json, now],
  );
  return response(receipt_json);
}
