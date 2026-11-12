/** Product conversation controls needed by the shared-agent comparison. */
import { homedir } from 'node:os';
import { join } from 'node:path';
import type { FastifyPluginAsync, FastifyReply } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwner } from '../core/authority/identity.ts';
import { HttpError } from '../core/http/errors.ts';
import { dictionaryBody } from '../list_reads/http.ts';
import { conversation_link_lock } from '../runtime/conversation_start.ts';
import {
  backend_supports_steer,
  ConversationAlreadyStarted,
  ConversationRoleMaterials,
  ConversationStartRequest,
} from './contracts.ts';
import type { ConversationAccess, ConversationBackendKey } from './contracts.ts';
import { PermissionAskedEventPayload, UserInputRequestedEventPayload } from './events.ts';
import { conversation_message_content, delivery_fate_json, parse_owner_send_body } from './http.ts';
import {
  message_content_json_entries,
  MessageContentEmpty,
  MessageContentNotPieces,
} from './message_content.ts';
import { resolve_conversation_start_request } from './logic/conversation_start_resolution.ts';
import type { SqliteProcessConversationSystem } from './system.ts';
import type { ConversationViewRuntime } from './views/routes.ts';

export interface ConversationHttpRuntime {
  readonly core: SqliteProcessConversationSystem;
  readonly record: ConversationViewRuntime;
}
function string_field(body: Record<string, unknown>, name: string): string {
  if (!(name in body))
    throw new HttpError(422, [
      { type: 'missing', loc: ['body', name], msg: 'Field required', input: body },
    ]);
  const value = body[name];
  if (typeof value !== 'string')
    throw new HttpError(422, [
      {
        type: 'string_type',
        loc: ['body', name],
        msg: 'Input should be a valid string',
        input: value,
      },
    ]);
  return value;
}
function optional_string(body: Record<string, unknown>, name: string): string | null {
  return body[name] == null ? null : string_field(body, name);
}
function start_request(body: Record<string, unknown>): ConversationStartRequest {
  const role_text = optional_string(body, 'role_text');
  const stated_identity = body.identity_environment_variables;
  const identity: [string, string][] = [];
  if (stated_identity !== undefined && stated_identity !== null) {
    if (typeof stated_identity !== 'object' || Array.isArray(stated_identity))
      throw new HttpError(422, 'identity_environment_variables must be a dictionary');
    for (const [key, value] of Object.entries(stated_identity)) {
      if (typeof value !== 'string')
        throw new HttpError(422, 'identity environment variables must be text');
      identity.push([key, value]);
    }
  }
  if (role_text === null && identity.length > 0)
    throw new HttpError(
      422,
      'identity environment variables belong to a role, so role_text is required',
    );
  let workspace_folder = optional_string(body, 'workspace_folder');
  if (workspace_folder === '~') workspace_folder = homedir();
  else if (workspace_folder?.startsWith('~/'))
    workspace_folder = join(homedir(), workspace_folder.slice(2));
  const backend_key = optional_string(body, 'backend_key');
  if (backend_key !== null && !['hermes', 'codex', 'claude'].includes(backend_key))
    throw new HttpError(422, "Input should be 'hermes', 'codex' or 'claude'");
  const access = optional_string(body, 'access');
  if (access !== null && access !== 'full') throw new HttpError(422, "Input should be 'full'");
  const request = new ConversationStartRequest({
    conversation_id: string_field(body, 'conversation_id'),
    model: string_field(body, 'model'),
    backend_key: backend_key as ConversationBackendKey | null,
    reasoning_effort: optional_string(body, 'reasoning_effort'),
    role_materials:
      role_text === null
        ? null
        : new ConversationRoleMaterials({ role_text, identity_environment_variables: identity }),
    workspace_folder,
    access: access as ConversationAccess | null,
  });
  try {
    resolve_conversation_start_request(request);
  } catch (error) {
    if (error instanceof Error) throw new HttpError(422, error.message);
    throw error;
  }
  return request;
}
function mutable_conversation(runtime: ConversationHttpRuntime, conversation_id: string): void {
  const associated = runtime.record.database.read((sql) =>
    sql.get<{ conversation_id: string | null }>(
      'SELECT tickets.conversation_id FROM ticket_conversations JOIN tickets ' +
        'ON tickets.id=ticket_conversations.ticket_id WHERE ticket_conversations.conversation_id=?',
      [conversation_id],
    ),
  );
  if (associated && associated.conversation_id !== conversation_id)
    throw new HttpError(409, "the conversation is not the Ticket's active conversation");
}
function unassociated_send(runtime: ConversationHttpRuntime, conversation_id: string): void {
  const associated = runtime.record.database.read((sql) =>
    sql.get(
      'SELECT 1 FROM ticket_conversations WHERE conversation_id=? ' +
        'UNION ALL SELECT 1 FROM agents WHERE conversation_id=? LIMIT 1',
      [conversation_id, conversation_id],
    ),
  );
  const record = runtime.record.store.read_conversation(conversation_id);
  const actor = new Map(record?.identity_environment_variables ?? []).get('PLAN_ACTOR');
  if (associated || (actor !== undefined && ['worker', 'chief', 'manager'].includes(actor)))
    throw new HttpError(409, 'employee conversations accept messages only through Send Message');
}
async function state_json(
  runtime: ConversationHttpRuntime,
  conversation_id: string,
): Promise<Record<string, unknown>> {
  const record = runtime.record.store.read_conversation(conversation_id);
  if (record === null) throw new HttpError(404, `no conversation ${conversation_id}`);
  const is_running = await runtime.core.is_running(conversation_id);
  const held_prompts = await runtime.core.held_prompts(conversation_id);
  const permissions = await runtime.core.pending_permission_ask_ids(conversation_id);
  const permission_events =
    permissions.size > 0
      ? [...runtime.record.store.read_events_after(conversation_id, 0)].reverse()
      : [];
  const permission = permission_events.find(
    (event) =>
      event.payload instanceof PermissionAskedEventPayload && permissions.has(event.payload.ask_id),
  )?.payload;
  const inputs = await runtime.core.pending_user_input_request_ids(conversation_id);
  const input_events =
    inputs.size > 0
      ? [...runtime.record.store.read_events_after(conversation_id, 0)].reverse()
      : [];
  const input = input_events.find(
    (event) =>
      event.payload instanceof UserInputRequestedEventPayload &&
      inputs.has(event.payload.request_id),
  )?.payload;
  return {
    conversation_id: record.conversation_id,
    backend_key: record.backend_key,
    supports_steer: backend_supports_steer(record.backend_key),
    model: record.model,
    reasoning_effort: record.reasoning_effort,
    workspace_folder:
      record.workspace_path_policy === 'executor_default' ? null : record.workspace_folder,
    workspace_path_policy: record.workspace_path_policy,
    access: record.access,
    latest_sequence: record.latest_sequence,
    owner_read_through_sequence: record.owner_read_through_sequence,
    is_running,
    held_prompts: held_prompts.map((held) => ({
      held_prompt_id: held.held_prompt_id,
      ...message_content_json_entries(held.content),
      sender_label: held.sender_label,
      sender_message_id: held.sender_message_id,
      sent_at_unix_milliseconds: held.sent_at_unix_milliseconds,
      sender: held.sender,
      recipient: held.recipient,
      queue_reason: held.queue_reason,
      delivery_uncertain: held.delivery_uncertain,
    })),
    pending_permission_ask:
      permission instanceof PermissionAskedEventPayload
        ? {
            ask_id: permission.ask_id,
            title: permission.title,
            detail: permission.detail,
            options: permission.options.map((option) => ({
              option_id: option.option_id,
              label: option.label,
              option_kind: option.option_kind,
            })),
          }
        : null,
    pending_user_input:
      input instanceof UserInputRequestedEventPayload
        ? {
            request_id: input.request_id,
            questions: input.questions.map((question) => ({
              question_id: question.question_id,
              header: question.header,
              question: question.question,
              options: question.options.map((option) => ({
                label: option.label,
                description: option.description,
              })),
              multi_select: question.multi_select,
              allow_other: question.allow_other,
            })),
          }
        : null,
  };
}
function direct_send_body(body: unknown, reply: FastifyReply) {
  if (!dictionaryBody(body, reply)) return null;
  // Direct SendBody carries model_change/reasoning_effort_change, unlike owner-send configuration.
  const adapted: Record<string, unknown> = {};
  for (const field of [
    'content',
    'sender_label',
    'mode',
    'sender_message_id',
    'sent_at_unix_milliseconds',
  ]) {
    if (field in body) adapted[field] = body[field];
  }
  if ('model_change' in body) adapted.model = body.model_change;
  if ('reasoning_effort_change' in body) adapted.reasoning_effort = body.reasoning_effort_change;
  return parse_owner_send_body(adapted, reply);
}
function send_error(error: unknown): never {
  if (
    error instanceof MessageContentEmpty ||
    error instanceof MessageContentNotPieces ||
    (error instanceof Error &&
      error.message === 'sender_message_id already names a different message')
  )
    throw new HttpError(422, error.message);
  throw error;
}
export function createConversationRoutes(
  runtime: ConversationHttpRuntime,
): FastifyPluginAsync<AreaServices> {
  return async (app, services) => {
    app.post('/conversations', async (request, reply) => {
      if (!dictionaryBody(request.body, reply)) return;
      const start = start_request(request.body);
      try {
        await runtime.core.start_conversation(start);
      } catch (error) {
        if (error instanceof ConversationAlreadyStarted)
          throw new HttpError(409, `conversation ${start.conversation_id} already exists`);
        throw error;
      }
      reply.code(201);
      return state_json(runtime, start.conversation_id);
    });
    app.get<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id',
      async (request) => state_json(runtime, request.params.conversation_id),
    );
    app.post<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/send',
      async (request, reply) => {
        const body = direct_send_body(request.body, reply);
        if (body === null) return;
        const id = request.params.conversation_id;
        return conversation_link_lock(`conversation:${id}`).run(async () => {
          mutable_conversation(runtime, id);
          unassociated_send(runtime, id);
          if (body.content.some((piece) => piece.piece === 'video_upload'))
            throw new HttpError(503, 'video uploads are unavailable');
          try {
            const content = await conversation_message_content(
              runtime.core.options.message_files,
              id,
              body.content,
              { sender_message_id: body.sender_message_id },
            );
            return delivery_fate_json(
              await runtime.core.send(id, content, {
                sender_label: body.sender_label,
                sender: services.identity(request).principal,
                mode: body.mode,
                model_change: body.model,
                reasoning_effort_change: body.reasoning_effort,
                sender_message_id: body.sender_message_id,
                sent_at_unix_milliseconds: body.sent_at_unix_milliseconds,
              }),
            );
          } catch (error) {
            send_error(error);
          }
        });
      },
    );
    app.post<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/interrupt',
      async (request, reply) => {
        mutable_conversation(runtime, request.params.conversation_id);
        await runtime.core.interrupt(request.params.conversation_id);
        reply.code(204).send();
      },
    );
    app.post<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/kill',
      async (request, reply) => {
        mutable_conversation(runtime, request.params.conversation_id);
        await runtime.core.kill(request.params.conversation_id);
        reply.code(204).send();
      },
    );
    app.post<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/permission-answers',
      async (request, reply) => {
        if (!dictionaryBody(request.body, reply)) return;
        requireOwner(services.identity(request));
        mutable_conversation(runtime, request.params.conversation_id);
        return {
          fate: await runtime.core.answer_permission_ask(
            request.params.conversation_id,
            string_field(request.body, 'ask_id'),
            string_field(request.body, 'option_id'),
          ),
        };
      },
    );
  };
}
