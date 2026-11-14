/** Bind canonical agent mutations and native message dispatch to one inbox position. */
import type { Clock } from '../core/clock.ts';
import type { Principal } from '../core/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { TransactionSql } from '../core/database/contracts.ts';
import { admitCommand } from '../core/authority/intake.ts';
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
  principal as message_principal,
  message_text,
  message_delivery_mode,
  result_json,
} from '../message_delivery/routes.ts';
import { ENTRIES } from '../membership/collections.ts';
import { write_create_ticket, write_patch_ticket } from '../tickets/routes.ts';
import { file_current_proposal, read_ticket } from '../tickets/data/index.ts';
import { ticket_json } from '../tickets/views.ts';
import { notify_review_transfers } from '../tickets/review_transfer.ts';
import type { ReviewTransferNotice } from '../tickets/contracts.ts';
import { open_child_assignment } from './data/child_custody.ts';
import { InboxStore } from './data/inbox.ts';
import type { PublishedFiles } from './data/inbox.ts';
import type { InboxRuns, PublishFiles } from './vps_inbox.ts';
import type { WireValue } from './contracts.ts';
import { require_current_incarnation_in_transaction } from './vps_presence_store.ts';
import { decode_value, ExecutorValidationError } from './protocol.ts';
import { now_ms } from './clock.ts';
import { is_record as is_json_object } from './../core/records.ts';

type Json = WireValue<'JsonObject'>;
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
  throw new PlannerError('validation', 'unsupported transactional executor mutation');
}
export class AgentWrites {
  readonly store: InboxStore;
  readonly clock: Clock;
  readonly runs: InboxRuns;
  conversations!: ConversationSystem;
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
    const principal = await this.runs.authority(write.child);
    const published = await publish();
    if (write.operation === 'message.send' || write.operation === 'ticket.request_help')
      return this.deliver_message(envelope, write, principal, identity, published);
    const notices: ReviewTransferNotice[] = [];
    const receipt = this.store.accept(
      envelope,
      (tx) => {
        const assignment = open_child_assignment(tx, write.child);
        if (assignment?.[0] !== principal.kind || assignment[1] !== principal.id)
          throw new PlannerError('agent_forbidden', 'child assignment changed');
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
  private help_recipient(
    tx: TransactionSql,
    write: WireValue<'CliWrite'>,
    principal: Principal,
  ): Principal {
    const ticket_id = write.arguments.ticket_id;
    if (typeof ticket_id !== 'string' || principal.kind !== 'ticket' || principal.id !== ticket_id)
      throw new PlannerError('agent_forbidden', 'only the Ticket Worker can request help');
    return read_ticket(tx, ticket_id).ceiling_holder;
  }
  private async deliver_message(
    envelope: WireValue<'QueuedEnvelope'>,
    write: WireValue<'CliWrite'>,
    principal: Principal,
    identity: ExecutorIdentity,
    published: PublishedFiles | null,
  ): Promise<WireValue<'AcceptanceReceipt'>> {
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
    return this.store.database.write((tx) => this.finish_dispatch(tx, envelope, result));
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
      require_current_incarnation_in_transaction(tx, identity.executor_incarnation);
      const previous = this.store.prior_receipt(tx, envelope, identity);
      if (previous !== null) return { previous, recipient: principal, message: '', mode: 'queue' };
      const body = write.arguments.body;
      if (!is_json_object(body))
        throw new PlannerError('validation', 'message body must be an object');
      const message = message_text(body.message);
      if (
        write.operation === 'ticket.request_help' &&
        Object.keys(body).some((name) => !['message', 'recipient'].includes(name))
      )
        throw new PlannerError('validation', 'unknown help request field');
      const mode = Object.hasOwn(body, 'mode')
        ? message_delivery_mode(body.mode)
        : message_delivery_mode();
      const recipient =
        write.operation === 'message.send'
          ? message_principal(body.target)
          : (() => {
              const fallback = this.help_recipient(tx, write, principal);
              return body.recipient ? message_principal(body.recipient) : fallback;
            })();
      admitCommand(
        tx,
        principal,
        'POST',
        write.operation === 'message.send'
          ? '/api/messages/send'
          : '/api/tickets/{ticket_id}/request-help',
        { ticket_id: write.arguments.ticket_id as string },
        body,
      );
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
      disposition: Object.hasOwn(result, 'error')
        ? 'refused'
        : result.fate === 'uncertain'
          ? 'uncertain'
          : 'accepted',
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
    this.finish_dispatch(tx, envelope, {
      target: { kind: recipient.kind, id: recipient.id },
      conversation_id,
      ...fate,
    });
  }
}
