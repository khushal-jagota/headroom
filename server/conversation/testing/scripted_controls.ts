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
import { live_status_before_adoption, PendingSteerProbe } from './scripted_probes.ts';
import { TerminalSaveProbe } from './scripted_terminal_save.ts';
import type { ScriptedTicketChoiceFault } from './scripted_ticket_choice.ts';

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
  ticket_choice_fault: ScriptedTicketChoiceFault,
): readonly Area[] {
  const core = context.core;
  if (!core) throw new Error('Scripted controls require the real conversation core');
  const pending = new PendingSteerProbe(context, registry);
  const terminal = new TerminalSaveProbe(context, registry);
  return [
    {
      prefix: '/api/test',
      routes: async (app) => {
        app.addHook('onClose', async () => {
          ticket_choice_fault.close();
          await pending.close();
          await terminal.close();
          await registry.close();
        });
        const settle = () => core.wait_until_quiescent();
        const identity = (request: { params: unknown }) =>
          (request.params as { conversation_id: string }).conversation_id;
        app.post('/signals/:conversation_id/ticket-choice-failure', async (request) =>
          ticket_choice_fault.arm(identity(request)),
        );
        app.get('/signals/:conversation_id/ticket-choice-failure', async (request) =>
          ticket_choice_fault.observe(identity(request)),
        );
        app.get('/signals/:conversation_id/live-status-before-adoption', async (request) =>
          live_status_before_adoption(context, identity(request)),
        );
        app.post<{ Body: { text: string; recipient: Principal; barrier: string } }>(
          '/signals/:conversation_id/pending-steer/begin',
          async (request) => pending.begin(identity(request), request.body),
        );
        app.get('/signals/:conversation_id/pending-steer/observe', async (request) =>
          pending.observe(identity(request)),
        );
        app.post('/signals/:conversation_id/pending-steer/release', async (request) =>
          pending.release(identity(request)),
        );
        app.post('/signals/:conversation_id/terminal-save/arm', async (request) =>
          terminal.arm(identity(request)),
        );
        app.post('/signals/:conversation_id/terminal-save/begin', async (request) =>
          terminal.begin(identity(request)),
        );
        app.post('/signals/:conversation_id/terminal-save/join', async (request) =>
          terminal.join(identity(request)),
        );
        app.post<{ Body: { enabled: boolean } }>(
          '/signals/:conversation_id/saved-model-enablement',
          async (request) => {
            const record = context.record.store.read_conversation(identity(request));
            if (!record) throw new Error('No conversation for saved model enablement');
            context.database.write((tx) =>
              tx.run(
                'INSERT INTO backend_model_enablement(backend_key,model_id,enabled) VALUES(?,?,?) ' +
                  'ON CONFLICT(backend_key,model_id) DO UPDATE SET enabled=excluded.enabled',
                [record.backend_key, record.model, request.body.enabled ? 1 : 0],
              ),
            );
            return { configured: true };
          },
        );
        app.post<{ Body: { ticket_id: string } }>(
          '/signals/:conversation_id/maintenance-mixed-messages',
          async (request) => {
            const ticket: Principal = { kind: 'ticket', id: request.body.ticket_id };
            await core.send(identity(request), text_message_content('owner message'), {
              sender_label: 'owner',
              sender: OWNER,
              recipient: ticket,
            });
            await core.send(identity(request), text_message_content('worker opener'), {
              sender_label: `Ticket ${ticket.id}`,
              sender: ticket,
              recipient: OWNER,
            });
            await settle();
            return { ok: true };
          },
        );
        app.post<{ Body: { ticket_id: string } }>(
          '/signals/:conversation_id/discard-source-messages',
          async (request) => {
            await core.send(identity(request), text_message_content('first held'), {
              sender_label: 'owner',
              sender_message_id: 'message-one',
              sent_at_unix_milliseconds: 1234,
              sender: { kind: 'ticket', id: request.body.ticket_id },
              recipient: { kind: 'chief', id: 'chief' },
            });
            await core.send(identity(request), text_message_content('second held'), {
              sender_label: 'owner',
              sender_message_id: 'message-two',
            });
            await settle();
            return { ok: true };
          },
        );
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
        app.post<{
          Body: {
            kind: Principal['kind'];
            id: string;
            label: string;
            text?: string;
            sender_message_id?: string | null;
            sent_at_unix_milliseconds?: number | null;
            settle?: boolean;
          };
        }>('/signals/:conversation_id/message', async (request, reply) => {
          const body = request.body;
          if (body.settle === false) pending.require(identity(request));
          try {
            await core.record_message_to_owner(
              identity(request),
              text_message_content(body.text ?? 'Explicit message to owner'),
              {
                sender_label: body.label,
                sender: { kind: body.kind, id: body.id },
                recipient: OWNER,
                sender_message_id: body.sender_message_id,
                sent_at_unix_milliseconds: body.sent_at_unix_milliseconds,
              },
            );
          } catch (error) {
            if (
              !(error instanceof Error) ||
              error.message !== 'sender_message_id already names a different message'
            ) {
              throw error;
            }
            return reply.code(422).send({ detail: error.message });
          }
          if (body.settle !== false) await settle();
          return { ok: true };
        });
        app.post<{ Body: { events: Record<string, unknown>[]; turn?: null; settle?: boolean } }>(
          '/signals/:conversation_id/backend-news',
          async (request, reply) => {
            const child = registry.child(identity(request));
            if (request.body.settle === false) pending.require(identity(request));
            const snapshot = await child.inspect();
            const session = 'turn' in request.body;
            if (session && request.body.turn !== null) {
              return reply.code(422).send({ detail: 'scripted session turn must be null' });
            }
            if (!session && snapshot.token === null)
              throw new Error('A scripted turn must exist before backend news');
            for (const scripted of request.body.events) {
              await child.emit(
                decode_scripted_event(backend_news(scripted)),
                session ? null : snapshot.token,
              );
            }
            if (request.body.settle === false) {
              pending.ending(
                identity(request),
                (async () => {
                  await child.flush_events();
                  await settle();
                })(),
              );
            } else {
              await child.flush_events();
              await settle();
            }
            return { reported: request.body.events.length };
          },
        );
        app.post('/signals/:conversation_id/sweep-idle-children', async () => {
          await core.sweep_idle_children();
          await settle();
          return { ok: true };
        });
        app.post<{
          Body: { command: string; behavior: ScriptedBehavior; target?: 'current' | 'next' };
        }>('/signals/:conversation_id/configure', async (request, reply) => {
          const target = request.body.target === undefined ? 'current' : request.body.target;
          if (target !== 'current' && target !== 'next') {
            return reply.code(422).send({ detail: 'invalid scripted child target' });
          }
          await registry.configure(
            identity(request),
            request.body.command,
            request.body.behavior,
            target,
          );
          return { configured: true };
        });
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
