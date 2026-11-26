/** Bind canonical agent mutations and native message dispatch to one inbox position. */
import { IncomingMessage } from 'node:http';
import { ADMITTED_REQUEST_SCOPE } from '../core/http/lifetime.ts';
import type { FastifyInstance, InjectOptions } from 'fastify';
import type { Clock } from '../core/clock.ts';
import type { Principal, RequestContext } from '../core/contracts.ts';
import { IN_PROCESS_REQUEST_CONTEXT } from '../core/authority/identity.ts';
import { failure_payload } from '../cli/http.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { admitCommand, unidentifiedCommandRefusal } from '../core/authority/intake.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';
import type {
  ConversationSystem,
  NativeMessageAdmission,
  NativeMessageOperation,
  PromptDeliveryMode,
} from '../conversation/contracts.ts';
import {
  MessageToOwnerEventPayload,
  PromptEventPayload,
  PromptDeliveryUncertainEventPayload,
  PromptDeliveryRefusedEventPayload,
  PromptDiscardedEventPayload,
} from '../conversation/events.ts';
import { delivery_fate_json } from '../conversation/http.ts';
import { send_message } from '../message_delivery/service.ts';
import {
  read_agent_message,
  replay_agent_message,
  retain_published_message_files,
  write_agent_message_acceptance,
} from '../message_delivery/data/agent_acceptances.ts';
import {
  principal as message_principal,
  message_text,
  message_delivery_mode,
  result_json,
} from '../message_delivery/routes.ts';
import { ENTRIES } from '../membership/collections.ts';
import {
  write_create_ticket,
  write_patch_ticket,
  write_reject_ticket_proposal,
} from '../tickets/routes.ts';
import { file_current_proposal } from '../tickets/data/index.ts';
import { ticket_json } from '../tickets/views.ts';
import { notify_review_transfers } from '../tickets/review_transfer.ts';
import type { ReviewTransferNotice } from '../tickets/contracts.ts';
import { current_agent_owner } from './data/child_custody.ts';
import { InboxStore } from './data/inbox.ts';
import type { PublishedFiles } from './data/inbox.ts';
import type { InboxRuns, PublishFiles } from './vps_inbox.ts';
import type { WireValue } from './contracts.ts';
import type { JsonValue } from './wire.generated.ts';
import { require_current_incarnation_in_transaction } from './vps_presence_store.ts';
import { decode_value, ExecutorValidationError } from './protocol.ts';
import { now_ms } from './clock.ts';
import { is_record as is_json_object } from './../core/records.ts';

type Json = WireValue<'JsonObject'>;
/** Any agent write without a transactional operation. The home runs its ordinary route. */
export const CLI_REQUEST = 'cli.request';
const RELAYED_WRITE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
/**
 * Saved before the route runs. It stays when the home stops, or the route runs out of time,
 * before the route answers.
 */
const UNFINISHED_REQUEST: Json = {
  error: {
    code: 'uncertain',
    message:
      'Home did not confirm that this request finished, so its effect is unknown. Check the current state before you repeat it.',
    detail: {},
  },
};
interface AgentRequest {
  readonly method: string;
  readonly url: string;
  readonly body: Json | null;
}
/** A local generic agent has no launch identity, so its relayed requests have none either. */
export function agent_request_context(
  principal: { readonly kind: string; readonly id: string },
  conversation_id: string,
): RequestContext {
  return principal.kind === 'conversation'
    ? { principal: { kind: 'unidentified', id: 'unidentified' }, conversationId: null }
    : { principal: principal as Principal, conversationId: conversation_id };
}
/** Inside the write: the child still acts for the principal that its request was judged as. */
function require_same_agent_owner(
  sql: ReadSql,
  child: WireValue<'ChildReference'>,
  principal: Principal,
): void {
  const current = agent_request_context(current_agent_owner(sql, child), '').principal;
  if (current.kind !== principal.kind || current.id !== principal.id)
    throw new PlannerError('agent_forbidden', 'child assignment changed');
}
/** Run one route in this process as the agent. Ordinary route authority decides. */
export function inject_agent_request(
  app: FastifyInstance,
  context: RequestContext,
  request: AgentRequest,
  signal?: AbortSignal,
) {
  return app.inject({
    method: request.method as InjectOptions['method'],
    url: request.url,
    signal,
    ...(request.body === null
      ? {}
      : { payload: JSON.stringify(request.body), headers: { 'content-type': 'application/json' } }),
    Request: class extends IncomingMessage {
      get [ADMITTED_REQUEST_SCOPE]() {
        return app.request_lifetime;
      }
      get [IN_PROCESS_REQUEST_CONTEXT]() {
        return context;
      }
    },
  });
}
/** A JSON answer is data. A file is text whatever it holds, as `send_text` reads it locally. */
function json_answer(headers: Record<string, unknown>): boolean {
  const media = String(headers['content-type'] ?? '')
    .split(';')[0]!
    .trim()
    .toLowerCase();
  return (
    headers['content-disposition'] === undefined &&
    (media === 'application/json' || /^application\/.+\+json$/.test(media))
  );
}
/** The JSON that the agent's CLI receives, the same as it decodes from the local route. */
export function agent_response_json(
  status: number,
  body: string,
  headers: Record<string, unknown>,
): Json {
  let data: unknown = null;
  let parsed = true;
  try {
    data = JSON.parse(body);
  } catch {
    parsed = false;
  }
  const refused = is_json_object(data) && Object.hasOwn(data, 'error');
  if (status < 200 || status >= 300 || refused) {
    const payload = failure_payload(status, data, body) as Json;
    // A receipt and an IPC error carry an error object, never bare text.
    return is_json_object(payload.error)
      ? payload
      : (failure_payload(status, { detail: data }, body) as Json);
  }
  if (!parsed || !json_answer(headers)) return { text: body };
  return is_json_object(data) ? (data as Json) : { data: data as JsonValue };
}
function agent_request(args: Json): AgentRequest {
  const { method, path, body } = args;
  if (typeof method !== 'string' || !RELAYED_WRITE_METHODS.has(method))
    throw new PlannerError('validation', 'an agent request needs a write method');
  if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//'))
    throw new PlannerError('validation', 'an agent request path must be a server path');
  if (body !== null && !is_json_object(body))
    throw new PlannerError('validation', 'an agent request body must be a JSON object or null');
  const target = new URL(path, 'http://executor');
  return { method, url: target.pathname + target.search, body };
}
/** Effects use the caller's writer. Canonical domain writers own all mutation rules. */
export function write_ticket_mutation(
  tx: TransactionSql,
  write: WireValue<'CliWrite'>,
  principal: Principal,
  clock: Clock,
  review_transfer_notices: ReviewTransferNotice[] = [],
): Json {
  const args = write.arguments;
  const body = args.body;
  if (!is_json_object(body)) throw new PlannerError('validation', 'ticket mutation needs a body');
  let route = '/api/tickets/{ticket_id}',
    method = 'PATCH';
  let params: Record<string, string> = { ticket_id: args.ticket_id as string };
  if (write.operation === 'ticket.create') {
    route = '/api/tickets';
    method = 'POST';
    params = {};
  } else if (write.operation === 'ticket.propose') {
    route += '/propose';
    method = 'POST';
  } else if (write.operation === 'ticket.send_back') {
    route += '/reject';
    method = 'POST';
  } else if (write.operation === 'ticket.block' || write.operation === 'ticket.unblock') {
    route = '/api/collections/{collection}/{container_id}/{member_id}';
    method = write.operation === 'ticket.block' ? 'PUT' : 'DELETE';
    params = {
      collection: 'blockers',
      container_id: args.ticket_id as string,
      member_id: args.blocker_id as string,
    };
  }
  admitCommand(tx, principal, method, route, params, body);
  if (write.operation === 'ticket.block' || write.operation === 'ticket.unblock') {
    const ticket_id = args.ticket_id,
      blocker_id = args.blocker_id;
    if (typeof ticket_id !== 'string' || typeof blocker_id !== 'string')
      throw new PlannerError('validation', 'blocker IDs must be text');
    ENTRIES.blockers[write.operation === 'ticket.block' ? 'add' : 'remove']({
      tx,
      principal,
      container_id: ticket_id,
      member_id: blocker_id,
      now: clock.nowUnix(),
      review_transfer_notices,
    });
    return { collection: 'blockers', container_id: ticket_id, member_id: blocker_id, ok: true };
  }
  if (write.operation === 'ticket.create')
    return write_create_ticket(tx, body, principal, clock.nowUnix()) as Json;
  const ticket_id = args.ticket_id;
  if (typeof ticket_id !== 'string')
    throw new PlannerError('validation', 'ticket mutation needs ticket_id');
  if (write.operation === 'ticket.propose') {
    if (principal.kind !== 'ticket' || principal.id !== ticket_id)
      throw new PlannerError('agent_forbidden', "only the Ticket's Worker can propose");
    if (typeof body.body !== 'string')
      throw new PlannerError('validation', 'proposal body must be text');
    return ticket_json(
      file_current_proposal(tx, ticket_id, { body: body.body, principal, now: clock.nowUnix() }),
      clock.nowUnix(),
    ) as Json;
  }
  if (write.operation === 'ticket.edit')
    return write_patch_ticket(
      tx,
      ticket_id,
      body,
      principal,
      clock.nowUnix(),
      review_transfer_notices,
    ) as Json;
  if (write.operation === 'ticket.send_back')
    return write_reject_ticket_proposal(tx, ticket_id, body, principal, clock.nowUnix()) as Json;
  throw new PlannerError('validation', 'unsupported transactional executor mutation');
}
export class AgentWrites {
  readonly store: InboxStore;
  readonly clock: Clock;
  readonly runs: InboxRuns;
  conversations!: ConversationSystem;
  app!: FastifyInstance;
  constructor(store: InboxStore, clock: Clock, runs: InboxRuns) {
    this.store = store;
    this.clock = clock;
    this.runs = runs;
  }
  async accept(
    envelope: WireValue<'QueuedEnvelope'>,
    identity: ExecutorIdentity,
    publish: PublishFiles,
  ): Promise<WireValue<'AcceptanceReceipt'>> {
    const write = decode_value(envelope.payload, 'CliWrite');
    if (write.child.conversation_id !== envelope.conversation_id)
      throw new ExecutorValidationError('CLI conversation conflicts with envelope');
    // A generic conversation acts as a local generic agent does: without a Panels identity.
    await this.runs.authority(write.child);
    const context = agent_request_context(
      this.store.database.read((sql) => current_agent_owner(sql, write.child)),
      write.child.conversation_id,
    );
    const principal = context.principal;
    if (write.operation === CLI_REQUEST)
      return this.relay_request(envelope, write, context, identity, await publish());
    // Its ordinary routes refuse such a write at the HTTP door. These operations refuse it here.
    if (principal.kind === 'unidentified') {
      const refusal = unidentifiedCommandRefusal(
        write.operation === 'message.send' ? write.arguments.body : null,
      );
      if (refusal !== null) throw refusal;
    }
    const published = await publish();
    if (write.operation === 'message.send')
      return this.deliver_message(envelope, write, principal, identity, published);
    const notices: ReviewTransferNotice[] = [];
    const receipt = this.store.accept(
      envelope,
      (tx) => {
        require_same_agent_owner(tx, write.child, principal);
        return write_ticket_mutation(tx, write, principal, this.clock, notices);
      },
      now_ms(),
      { identity, published },
    );
    if (receipt.disposition !== 'refused' && notices.length)
      await notify_review_transfers(this.conversations, this.store.database, notices, {
        now: this.clock.nowUnix(),
      });
    return receipt;
  }
  private async deliver_message(
    envelope: WireValue<'QueuedEnvelope'>,
    write: WireValue<'CliWrite'>,
    principal: Principal,
    identity: ExecutorIdentity,
    published: PublishedFiles | null,
  ): Promise<WireValue<'AcceptanceReceipt'>> {
    const body = write.arguments.body;
    if (is_json_object(body) && message_principal(body.target).kind === 'owner')
      return this.accept_owner_message(envelope, write, principal, identity, published);
    const reserved = this.reserve_dispatch(envelope, write, principal, identity, published);
    if (reserved.previous !== null) return reserved.previous;
    const { recipient, message, mode } = reserved;
    const operation: NativeMessageOperation = {
      sender_id: envelope.sender_id,
      submission_id: envelope.submission_id,
      in_commit: (tx, conversation_id, payload, admission) =>
        this.record_delivery(envelope, recipient, tx, conversation_id, payload, admission),
    };
    let result: Json;
    try {
      result = result_json(
        await send_message(
          this.conversations,
          this.store.database,
          this.clock,
          { principal, conversationId: null },
          recipient,
          message,
          mode,
          { sender_message_id: envelope.submission_id, recording_receipt: operation },
        ),
      ) as Json;
    } catch (error) {
      if (!(error instanceof PlannerError)) throw error;
      result = error.toPayload() as Json;
    }
    // Reservation admitted the native delivery. Its known outcome survives replacement.
    return this.store.database.write((tx) =>
      this.finish_dispatch(tx, envelope, result, delivery_disposition(result)),
    );
  }
  private async accept_owner_message(
    envelope: WireValue<'QueuedEnvelope'>,
    write: WireValue<'CliWrite'>,
    principal: Principal,
    identity: ExecutorIdentity,
    published: PublishedFiles | null,
  ): Promise<WireValue<'AcceptanceReceipt'>> {
    const previous = this.store.preflight(envelope, { identity });
    if (previous !== null) return previous;
    const body = write.arguments.body;
    if (!is_json_object(body))
      throw new PlannerError('validation', 'message body must be an object');
    const recipient = message_principal(body.target);
    const message = message_text(body.message);
    const mode = Object.hasOwn(body, 'mode')
      ? message_delivery_mode(body.mode)
      : message_delivery_mode();
    const files = await retain_published_message_files(published);
    const operation: NativeMessageOperation = {
      sender_id: envelope.sender_id,
      submission_id: envelope.submission_id,
      in_commit: (tx, conversation_id) => {
        require_current_incarnation_in_transaction(
          tx,
          identity.executor_incarnation,
          this.store.machine_id,
        );
        require_same_agent_owner(tx, write.child, principal);
        if (conversation_id !== envelope.conversation_id)
          throw new PlannerError(
            'agent_forbidden',
            'sender conversation changed before message acceptance',
          );
        const replay = replay_agent_message(tx, envelope, this.store.machine_id, principal);
        if (replay !== null) return;
        if (this.store.prior_receipt(tx, envelope, identity) !== null)
          throw new PlannerError('retry_later', 'Message position changed before acceptance');
        admitCommand(tx, principal, 'POST', '/api/messages/send', {}, body);
        this.store.require_file_owner(tx, envelope);
        this.store.require_unchanged_files(envelope, published);
        const receipt: WireValue<'AcceptanceReceipt'> = {
          sender_id: envelope.sender_id,
          submission_id: envelope.submission_id,
          disposition: 'accepted',
          accepted_at_vps_ms: now_ms(),
          result: {
            target: { kind: recipient.kind, id: recipient.id },
            conversation_id,
            fate: 'recorded',
          },
        };
        write_agent_message_acceptance(
          tx,
          {
            submission_id: envelope.submission_id,
            machine_id: this.store.machine_id,
            sender_id: envelope.sender_id,
            source_conversation_id: envelope.conversation_id,
            source: principal,
            envelope,
            recipient,
            mode,
            message,
            target_conversation_id: conversation_id,
            receipt,
            pending: false,
          },
          files,
        );
        this.store.write_position(tx, envelope, receipt);
      },
    };
    await send_message(
      this.conversations,
      this.store.database,
      this.clock,
      { principal, conversationId: envelope.conversation_id },
      recipient,
      message,
      mode,
      { sender_message_id: envelope.submission_id, recording_receipt: operation },
    );
    const accepted = this.store.database.read((sql) =>
      read_agent_message(sql, envelope.submission_id),
    );
    if (accepted === null) throw new Error('owner message completed without durable custody');
    return accepted.receipt;
  }
  /**
   * Exactly one route run per inbox position. The admitted route and receipt remain owned
   * together through graceful shutdown, even after its HTTP caller disconnects.
   * A process crash during an arbitrary external effect still leaves an uncertain receipt.
   */
  private async relay_request(
    envelope: WireValue<'QueuedEnvelope'>,
    write: WireValue<'CliWrite'>,
    context: RequestContext,
    identity: ExecutorIdentity,
    published: PublishedFiles | null,
  ): Promise<WireValue<'AcceptanceReceipt'>> {
    const reserved = this.store.database.write((tx) => {
      require_current_incarnation_in_transaction(
        tx,
        identity.executor_incarnation,
        this.store.machine_id,
      );
      const previous = this.store.prior_receipt(tx, envelope, identity);
      if (previous !== null) return { previous, request: null };
      const request = agent_request(write.arguments);
      require_same_agent_owner(tx, write.child, context.principal);
      this.store.require_file_owner(tx, envelope);
      this.store.require_unchanged_files(envelope, published);
      const unfinished: WireValue<'AcceptanceReceipt'> = {
        sender_id: envelope.sender_id,
        submission_id: envelope.submission_id,
        disposition: 'uncertain',
        accepted_at_vps_ms: now_ms(),
        result: UNFINISHED_REQUEST,
      };
      this.store.write_position(tx, envelope, unfinished, { dispatching: true });
      return { previous: unfinished, request };
    });
    if (reserved.request === null) return reserved.previous;
    // accept_items retains this complete operation, and endpoint shutdown joins it before
    // the database closes. An HTTP observation timeout must not discard the final receipt.
    const response = await inject_agent_request(this.app, context, reserved.request);
    const result = agent_response_json(response.statusCode, response.body, response.headers);
    const disposition = Object.hasOwn(result, 'error') ? 'refused' : 'accepted';
    return this.store.database.write((tx) =>
      this.finish_dispatch(tx, envelope, result, disposition),
    );
  }
  private reserve_dispatch(
    envelope: WireValue<'QueuedEnvelope'>,
    write: WireValue<'CliWrite'>,
    principal: Principal,
    identity: ExecutorIdentity,
    published: PublishedFiles | null,
  ): {
    previous: WireValue<'AcceptanceReceipt'> | null;
    recipient: Principal;
    message: string;
    mode: PromptDeliveryMode;
  } {
    return this.store.database.write((tx) => {
      require_current_incarnation_in_transaction(
        tx,
        identity.executor_incarnation,
        this.store.machine_id,
      );
      const previous = this.store.prior_receipt(tx, envelope, identity);
      if (previous !== null) return { previous, recipient: principal, message: '', mode: 'queue' };
      require_same_agent_owner(tx, write.child, principal);
      const body = write.arguments.body;
      if (!is_json_object(body))
        throw new PlannerError('validation', 'message body must be an object');
      const message = message_text(body.message);
      const mode = Object.hasOwn(body, 'mode')
        ? message_delivery_mode(body.mode)
        : message_delivery_mode();
      const recipient = message_principal(body.target);
      admitCommand(tx, principal, 'POST', '/api/messages/send', {}, body);
      this.store.require_file_owner(tx, envelope);
      this.store.require_unchanged_files(envelope, published);
      this.store.write_position(
        tx,
        envelope,
        {
          sender_id: envelope.sender_id,
          submission_id: envelope.submission_id,
          disposition: 'uncertain',
          accepted_at_vps_ms: now_ms(),
          result: {
            target: { kind: recipient.kind, id: recipient.id },
            conversation_id: null,
            fate: 'uncertain',
          },
        },
        { dispatching: true },
      );
      return { previous: null, recipient, message, mode };
    });
  }
  private finish_dispatch(
    tx: TransactionSql,
    envelope: WireValue<'QueuedEnvelope'>,
    result: Json,
    disposition: WireValue<'AcceptanceReceipt'>['disposition'],
  ): WireValue<'AcceptanceReceipt'> {
    const previous = tx.get<{ result_json: string; submission_id: string; disposition: string }>(
      'SELECT * FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?',
      [envelope.sender_id, envelope.conversation_id],
    );
    if (!previous || previous.submission_id !== envelope.submission_id)
      throw new ExecutorValidationError('message inbox position changed during dispatch');
    if (previous.disposition !== 'dispatching') return InboxStore.receipt(previous);
    const receipt: WireValue<'AcceptanceReceipt'> = {
      sender_id: envelope.sender_id,
      submission_id: envelope.submission_id,
      disposition,
      result,
      accepted_at_vps_ms: now_ms(),
    };
    this.store.write_position(tx, envelope, receipt);
    return receipt;
  }
  private record_delivery(
    envelope: WireValue<'QueuedEnvelope'>,
    recipient: Principal,
    tx: TransactionSql,
    conversation_id: string,
    payload: unknown,
    admission: NativeMessageAdmission | null,
  ): void {
    let fate: Json;
    if (payload instanceof MessageToOwnerEventPayload) fate = { fate: 'recorded' };
    else if (payload instanceof PromptEventPayload && admission !== null)
      fate = delivery_fate_json(admission.fate) as Json;
    else if (payload instanceof PromptDeliveryUncertainEventPayload) fate = { fate: 'uncertain' };
    else if (payload instanceof PromptDeliveryRefusedEventPayload)
      fate = { fate: 'refused', refusal_reason: payload.refusal_reason };
    else if (payload instanceof PromptDiscardedEventPayload) fate = { fate: 'discarded' };
    else return;
    const current = tx.get<{ submission_id: string; disposition: string }>(
      'SELECT submission_id,disposition FROM mac_inbox_positions WHERE sender_id=? AND conversation_id=?',
      [envelope.sender_id, envelope.conversation_id],
    );
    if (
      !current ||
      current.submission_id !== envelope.submission_id ||
      current.disposition !== 'dispatching'
    )
      return;
    const result: Json = {
      target: { kind: recipient.kind, id: recipient.id },
      conversation_id,
      ...fate,
    };
    this.finish_dispatch(tx, envelope, result, delivery_disposition(result));
  }
}
/** A message's receipt follows its delivery fate. */
function delivery_disposition(result: Json): WireValue<'AcceptanceReceipt'>['disposition'] {
  if (Object.hasOwn(result, 'error')) return 'refused';
  return result.fate === 'uncertain' ? 'uncertain' : 'accepted';
}
