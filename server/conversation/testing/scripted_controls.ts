/** Test-only controls for the shared scripted process and exact committed record rows. */
import type { Area } from '../../core/authority/register.ts';
import type { TestCompositionContext } from '../../core/start.ts';
import type { ReadSql } from '../../core/database/contracts.ts';
import { OWNER } from '../../core/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import { BACKEND_KEYS_SUPPORTING_STEER, require_conversation_backend_key } from '../contracts.ts';
import type { ConversationBackendKey } from '../contracts.ts';
import {
  MessageFile,
  MessageImage,
  MessageText,
  text_message_content,
} from '../message_content.ts';
import { decode_scripted_event } from './scripted_protocol.ts';
import type { ScriptedBehavior, ScriptedEvent } from './scripted_protocol.ts';
import type { ScriptedBackendRegistry } from './scripted_registry.ts';

const event_names: Readonly<Record<string, string>> = {
  tool_call_started: 'ToolCallStarted',
  tool_call_finished: 'ToolCallFinished',
  plan_updated: 'PlanUpdated',
  token_usage: 'TokenUsageReported',
  context_compacted: 'ContextCompacted',
  command_reply: 'CommandReplyReported',
  user_input_failed: 'UserInputFailed',
  turn_ended: 'TurnEnded',
  permission_asked: 'PermissionAskRaised',
};
function backend_news(scripted: Record<string, unknown>): ScriptedEvent {
  const { kind, ...fields } = scripted;
  const name = event_names[String(kind)] ?? String(kind);
  const values = fields.values === undefined ? fields : (fields.values as Record<string, unknown>);
  return {
    kind: name,
    values: kind === 'permission_asked' ? { ask: values } : values,
  };
}
function observed(value: unknown): unknown {
  if (value instanceof MessageText) return { piece: 'text', text: value.text };
  if (value instanceof MessageImage) return { piece: 'image', ...value };
  if (value instanceof MessageFile) return { piece: 'file', ...value };
  if (Array.isArray(value)) return value.map(observed);
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([name, item]) => [name, observed(item)]));
  }
  return value;
}
function row_json(row: Record<string, unknown>): Record<string, unknown> {
  const result = { ...row };
  for (const name of ['payload', 'identity_environment_variables', 'composer_catalog']) {
    if (typeof result[name] === 'string') result[name] = JSON.parse(result[name]);
  }
  return result;
}
function record_rows(sql: ReadSql, id: string) {
  const rows = (table: string, order: string) =>
    sql
      .all<Record<string, unknown>>(
        `SELECT * FROM ${table} WHERE conversation_id=? ORDER BY ${order}`,
        [id],
      )
      .map(row_json);
  return {
    conversation: rows('conversations', 'conversation_id')[0] ?? null,
    events: rows('conversation_events', 'sequence'),
    lifecycle: rows(
      'conversation_lifecycle_facts',
      'kind,turn_number,operation,provider_identity,status,identity',
    ),
    views: {
      state: rows('conversation_view_state', 'conversation_id'),
      rows: rows('conversation_view_rows', 'sequence'),
      turns: rows('conversation_view_turns', 'source_sequence,turn_key'),
      groups: rows('conversation_view_groups', 'source_sequence,group_key'),
      lookups: rows('conversation_view_lookups', 'lookup_kind,source_sequence,lookup_id'),
    },
  };
}

export function scriptedConversationControls(
  context: TestCompositionContext,
  registry: ScriptedBackendRegistry,
): readonly Area[] {
  const core = context.core;
  if (!core) throw new Error('Scripted controls require the real conversation core');
  return [
    {
      prefix: '/api/test',
      routes: async (app) => {
        app.addHook('onClose', () => registry.close());
        const settle = () => core.wait_until_quiescent();
        const identity = (request: { params: unknown }) =>
          (request.params as { conversation_id: string }).conversation_id;
        app.post('/signals/:conversation_id/finish', async (request) => {
          await registry.child(identity(request)).finish();
          await settle();
          return { ok: true };
        });
        app.post('/signals/:conversation_id/questions', async (request) => {
          await registry.child(identity(request)).request_questions();
          await settle();
          return { ok: true };
        });
        app.post('/signals/:conversation_id/reject-next-prompt', async (request) => {
          await registry.child(identity(request)).reject_next_prompt();
          return { ok: true };
        });
        app.post<{ Body: { kind: Principal['kind']; id: string; label: string; text?: string } }>(
          '/signals/:conversation_id/message',
          async (request) => {
            const body = request.body;
            await core.record_message_to_owner(
              identity(request),
              text_message_content(body.text ?? 'Explicit message to owner'),
              {
                sender_label: body.label,
                sender: { kind: body.kind, id: body.id },
                recipient: OWNER,
              },
            );
            await settle();
            return { ok: true };
          },
        );
        app.post<{ Body: { events: Record<string, unknown>[] } }>(
          '/signals/:conversation_id/backend-news',
          async (request) => {
            const child = registry.child(identity(request));
            const snapshot = await child.inspect();
            if (snapshot.token === null)
              throw new Error('A scripted turn must exist before backend news');
            for (const scripted of request.body.events) {
              await child.emit(decode_scripted_event(backend_news(scripted)), snapshot.token);
            }
            await child.flush_events();
            await settle();
            return { reported: request.body.events.length };
          },
        );
        app.post<{ Body: { command: string; behavior: ScriptedBehavior } }>(
          '/signals/:conversation_id/configure',
          async (request) => {
            await registry.configure(
              identity(request),
              request.body.command,
              request.body.behavior,
            );
            return { configured: true };
          },
        );
        app.get('/signals/:conversation_id/inspect', async (request) => {
          const id = identity(request);
          await settle();
          const children = await registry.inspect(id);
          const runtime = {
            running: await core.is_running(id),
            held_prompts: observed(await core.held_prompts(id)),
            pending_permission_ask_ids: [...(await core.pending_permission_ask_ids(id))].sort(),
            pending_user_input_request_ids: [
              ...(await core.pending_user_input_request_ids(id)),
            ].sort(),
          };
          return {
            ...context.database.read((sql) => record_rows(sql, id)),
            runtime,
            backend: { children },
          };
        });
        app.post<{ Body: { backend_keys: string[] } }>(
          '/conversation-steering',
          async (request) => {
            const keys = request.body.backend_keys.map(require_conversation_backend_key);
            const supported = BACKEND_KEYS_SUPPORTING_STEER as Set<ConversationBackendKey>;
            supported.clear();
            for (const key of keys) supported.add(key);
            return { configured: true };
          },
        );
      },
    },
  ];
}
