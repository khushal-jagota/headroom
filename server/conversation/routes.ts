/** Product conversation HTTP surface, sharing the record and process runtime. */
import { homedir } from 'node:os';
import { join } from 'node:path';
import { stat } from 'node:fs/promises';
import { PlannerError } from '../core/contracts.ts';
import { sendFile } from '../core/http/static.ts';
import { body_fields, Fields, object } from './http/intake.ts';
import { public_event, send_event_tail } from './http/record.ts';
import type { ConversationHttpWriteScope } from './http/writes.ts';
import { readable_tool_call_detail } from './tool_detail.ts';
import { MessageFileMissing } from './record/message_files.ts';
import type { FastifyPluginAsync, FastifyReply } from 'fastify';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwner } from '../core/authority/identity.ts';
import { HttpError } from '../core/http/errors.ts';
import { dictionaryBody, scalarQueries } from '../list_reads/http.ts';
import { conversation_link_lock } from '../runtime/conversation_start.ts';
import {
  backend_supports_steer,
  ConversationAlreadyStarted,
  ConversationRoleMaterials,
  ConversationStartRequest,
} from './contracts.ts';
import type { ConversationAccess, ConversationBackendKey } from './contracts.ts';
import {
  PermissionAskedEventPayload,
  UserInputRequestedEventPayload,
  ToolCallFinishedEventPayload,
  UserInputAnswer,
} from './events.ts';
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
  readonly writes: ConversationHttpWriteScope;
}
function start_request(raw: unknown): ConversationStartRequest {
  const fields = body_fields(raw);
  const conversation_id = fields.text('conversation_id');
  const model = fields.text('model');
  const backend_key = fields.choice('backend_key', ['hermes', 'codex', 'claude'], true, false);
  const reasoning_effort = fields.text('reasoning_effort', true, false);
  const role_text = fields.text('role_text', true, false);
  const identity: [string, string][] = [];
  const stated_identity = fields.body.identity_environment_variables;
  if (stated_identity != null) {
    if (!object(stated_identity))
      fields.issue(
        'dict_type',
        ['identity_environment_variables'],
        'Input should be a valid dictionary',
        stated_identity,
      );
    else
      for (const [key, value] of Object.entries(stated_identity)) {
        if (typeof value !== 'string')
          fields.issue(
            'string_type',
            ['identity_environment_variables', key],
            'Input should be a valid string',
            value,
          );
        else identity.push([key, value]);
      }
  }
  let workspace_folder = fields.text('workspace_folder', true, false);
  const access = fields.choice('access', ['full'], true, false);
  fields.finish();
  if (role_text === null && identity.length > 0)
    throw new HttpError(
      422,
      'identity environment variables belong to a role, so role_text is required',
    );
  if (workspace_folder === '~') workspace_folder = homedir();
  else if (workspace_folder?.startsWith('~/'))
    workspace_folder = join(homedir(), workspace_folder.slice(2));
  const request = new ConversationStartRequest({
    conversation_id: conversation_id!,
    model: model!,
    backend_key: backend_key as ConversationBackendKey | null,
    reasoning_effort,
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
function require_conversation(runtime: ConversationHttpRuntime, id: string) {
  const record = runtime.record.store.read_conversation(id);
  if (record === null) throw new HttpError(404, `no conversation ${id}`);
  return record;
}
function integer_queries(raw: unknown, names: readonly string[]) {
  const values = scalarQueries(object(raw) ? raw : {});
  const fields = new Fields(values, ['query']);
  const parsed: Record<string, number | null> = {};
  for (const name of names) parsed[name] = fields.integer(name, false);
  fields.finish();
  return parsed;
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
  const fields = body_fields(body);
  body = fields.body;
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
  return parse_owner_send_body(adapted, reply, {
    original_body: body,
    model: 'model_change',
    reasoning_effort: 'reasoning_effort_change',
  });
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
      '/conversations/:conversation_id/owner-read',
      async (request) => {
        const fields = body_fields(request.body);
        const through = fields.integer('through_sequence');
        fields.finish();
        requireOwner(services.identity(request));
        if (through! < 0) throw new HttpError(422, 'through_sequence must be non-negative');
        const record = await runtime.writes.run(() =>
          runtime.record.store.advance_owner_read_through_sequence(
            request.params.conversation_id,
            through!,
          ),
        );
        if (record === null)
          throw new HttpError(404, `no conversation ${request.params.conversation_id}`);
        return { owner_read_through_sequence: record.owner_read_through_sequence };
      },
    );
    app.get<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/composer-catalog',
      async (request) => ({
        composer_catalog: require_conversation(
          runtime,
          request.params.conversation_id,
        ).composer_catalog.map((entry) => ({
          kind: entry.kind,
          display_text: entry.display_text,
          insertion_text: entry.insertion_text,
          description: entry.description,
          argument_hint: entry.argument_hint,
        })),
      }),
    );
    app.get<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/events',
      async (request) => {
        const { after, before, limit } = integer_queries(request.query, [
          'after',
          'before',
          'limit',
        ]);
        if (limit !== null && (limit < 1 || limit > 100))
          throw new PlannerError('validation', 'limit must be between 1 and 100', { limit });
        if (before !== null && limit === null)
          throw new PlannerError('validation', 'before needs a limit: it reads backwards', {
            before,
          });
        if (before !== null && after !== null)
          throw new PlannerError('validation', 'a page reads one direction: after, or before', {
            after,
            before,
          });
        const id = request.params.conversation_id;
        const record = require_conversation(runtime, id);
        let events;
        let has_more;
        if (after === null && limit !== null) {
          events = runtime.record.store.read_latest_events(id, {
            before_sequence: before,
            limit: limit + 1,
          });
          has_more = events.length > limit;
          events = events.slice(-limit);
        } else {
          events = runtime.record.store.read_events_after(id, after || 0);
          has_more = limit !== null && events.length > limit;
          if (limit !== null) events = events.slice(0, limit);
        }
        return {
          events: events.map((event) => public_event(event, record.backend_key)),
          has_more,
          diagnostics_expired: runtime.record.store.diagnostics_expired(id),
        };
      },
    );
    app.get<{ Params: { conversation_id: string; sequence: string } }>(
      '/conversations/:conversation_id/events/:sequence/detail',
      async (request) => {
        const fields = new Fields({ sequence: request.params.sequence }, ['path']);
        const sequence = fields.integer('sequence');
        fields.finish();
        const id = request.params.conversation_id;
        const record = require_conversation(runtime, id);
        const event = runtime.record.store.read_event(id, sequence!);
        if (event === null && runtime.record.store.sequence_expired(id, sequence!))
          throw new PlannerError('diagnostic_expired', 'This diagnostic detail expired.');
        if (!(event?.payload instanceof ToolCallFinishedEventPayload))
          throw new HttpError(404, `conversation ${id} has no finished tool call at ${sequence}`);
        return {
          detail: readable_tool_call_detail(event.payload.detail, {
            backend_key: record.backend_key,
          }),
        };
      },
    );
    app.get<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/tail',
      { config: { uncompressed: true } },
      async (request, reply) => {
        const { after } = integer_queries(request.query, ['after']);
        const id = request.params.conversation_id;
        const record = require_conversation(runtime, id);
        return send_event_tail(runtime.record, id, record.backend_key, after ?? 0, reply);
      },
    );
    app.route<{ Params: { conversation_id: string; stored_file_id: string } }>({
      method: ['GET', 'HEAD'],
      url: '/conversations/:conversation_id/files/:stored_file_id',
      async handler(request, reply) {
        const { conversation_id, stored_file_id } = request.params;
        require_conversation(runtime, conversation_id);
        const files = runtime.core.options.message_files;
        let path;
        try {
          path = files.path_of(conversation_id, stored_file_id);
          await stat(path);
        } catch (error) {
          if (error instanceof MessageFileMissing || (error instanceof Error && 'code' in error))
            throw new HttpError(404, 'no such file');
          throw error;
        }
        let media_type =
          (await files.media_type_of(conversation_id, stored_file_id)) ??
          'application/octet-stream';
        if (media_type.startsWith('text/') && !media_type.toLowerCase().includes('charset='))
          media_type += '; charset=utf-8';
        reply.header('x-content-type-options', 'nosniff');
        return sendFile(request, reply, path, {
          mime: media_type,
          cache: 'private, max-age=31536000, immutable',
        });
      },
    });
    app.post('/voice-transcriptions', async (request) => {
      const fields = body_fields(request.body);
      fields.text('audio');
      fields.text('media_type', false, false);
      fields.extras(['audio', 'media_type']);
      fields.finish();
      // Audio processing and configured transcription belong to the uploads/service unit.
      throw new HttpError(503, 'voice transcription needs PLAN_GROQ_API_KEY, which is not set');
    });
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
    app.delete<{ Params: { conversation_id: string; held_prompt_id: string } }>(
      '/conversations/:conversation_id/held-prompts/:held_prompt_id',
      async (request) => {
        const { conversation_id, held_prompt_id } = request.params;
        mutable_conversation(runtime, conversation_id);
        return {
          discarded: await runtime.core.discard_held_prompt(conversation_id, held_prompt_id),
        };
      },
    );
    app.post<{ Params: { conversation_id: string; held_prompt_id: string } }>(
      '/conversations/:conversation_id/held-prompts/:held_prompt_id/promote',
      async (request) => {
        const fields = body_fields(request.body);
        const mode = fields.choice('mode', ['send_now', 'steer']);
        fields.finish();
        const { conversation_id, held_prompt_id } = request.params;
        mutable_conversation(runtime, conversation_id);
        const fate = await runtime.core.promote_held_prompt(
          conversation_id,
          held_prompt_id,
          mode as 'send_now' | 'steer',
        );
        return fate === null
          ? { promoted: false }
          : { promoted: true, ...delivery_fate_json(fate) };
      },
    );
    app.post<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/permission-answers',
      async (request) => {
        const fields = body_fields(request.body);
        const ask_id = fields.text('ask_id');
        const option_id = fields.text('option_id');
        fields.extras(['ask_id', 'option_id']);
        fields.finish();
        requireOwner(services.identity(request));
        mutable_conversation(runtime, request.params.conversation_id);
        return {
          fate: await runtime.core.answer_permission_ask(
            request.params.conversation_id,
            ask_id!,
            option_id!,
          ),
        };
      },
    );
    app.post<{ Params: { conversation_id: string } }>(
      '/conversations/:conversation_id/user-input-answers',
      async (request) => {
        const fields = body_fields(request.body);
        const request_id = fields.text('request_id');
        const answers: UserInputAnswer[] = [];
        const supplied = fields.body.answers;
        if (!('answers' in fields.body))
          fields.issue('missing', ['answers'], 'Field required', fields.body);
        else if (!object(supplied))
          fields.issue('dict_type', ['answers'], 'Input should be a valid dictionary', supplied);
        else
          for (const [question_id, value] of Object.entries(supplied)) {
            const nested = new Fields(value, ['body', 'answers', question_id]);
            if (object(value)) {
              const choices = value.answers;
              if (!('answers' in value))
                nested.issue('missing', ['answers'], 'Field required', value);
              else if (!Array.isArray(choices))
                nested.issue('list_type', ['answers'], 'Input should be a valid list', choices);
              else {
                choices.forEach((choice, index) => {
                  if (typeof choice !== 'string')
                    nested.issue(
                      'string_type',
                      ['answers', index],
                      'Input should be a valid string',
                      choice,
                    );
                });
                if (nested.issues.length === 0)
                  answers.push(new UserInputAnswer({ question_id, answers: choices as string[] }));
              }
            }
            fields.issues.push(...nested.issues);
          }
        fields.extras(['request_id', 'answers']);
        fields.finish();
        requireOwner(services.identity(request));
        mutable_conversation(runtime, request.params.conversation_id);
        return {
          fate: await runtime.core.answer_user_input(
            request.params.conversation_id,
            request_id!,
            answers,
          ),
        };
      },
    );
  };
}
