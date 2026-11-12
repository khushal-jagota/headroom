/** Existing isolated browser fixture controls, backed by the real conversation core. */
import type { Area } from '../../core/authority/register.ts';
import type { TestCompositionContext } from '../../core/start.ts';
import { OWNER } from '../../core/contracts.ts';
import type { Principal } from '../../core/contracts.ts';
import { write_ticket_conversation_start } from '../../tickets/data/index.ts';
import { ConversationRoleMaterials, ConversationStartRequest } from '../contracts.ts';
import { text_message_content } from '../message_content.ts';
import type { ScriptedBackendRegistry } from './scripted_registry.ts';
import { ticketModelCatalogControl } from '../snapshot/testing/ticket_models.ts';

export function browserConversationControls(
  context: TestCompositionContext,
  registry: ScriptedBackendRegistry,
): readonly Area[] {
  const core = context.core;
  if (!core) throw new Error('Browser controls require the real conversation core');
  const identity = (request: { params: unknown }) =>
    (request.params as { conversation_id: string }).conversation_id;
  if (!context.backendServices) throw new Error('Backend snapshot services are not composed');
  return [
    ticketModelCatalogControl(context.backendServices),
    {
      prefix: '/api/test',
      routes: async (app) => {
        app.post<{ Body: { ticket_id?: string } | null }>(
          '/signals/:conversation_id/start',
          async (request) => {
            const conversation_id = identity(request);
            const ticket_id = request.body?.ticket_id;
            if (context.record.store.read_conversation(conversation_id) === null) {
              await core.start_conversation(
                new ConversationStartRequest({
                  conversation_id,
                  model: 'gpt-6.1-sol',
                  role_materials:
                    ticket_id === undefined
                      ? null
                      : new ConversationRoleMaterials({
                          role_text: 'Bounded synthetic Ticket worker for the browser proof.',
                          identity_environment_variables: [
                            ['PLAN_ACTOR', 'worker'],
                            ['PLAN_TICKET_ID', ticket_id],
                          ],
                        }),
                }),
              );
            }
            if (ticket_id !== undefined) {
              context.database.write((tx) =>
                write_ticket_conversation_start(tx, ticket_id, {
                  conversation_id,
                  backend: 'codex',
                  model: 'gpt-6.1-sol',
                  reasoning_effort: null,
                  now: context.clock.nowUnix(),
                }),
              );
            }
            await core.send(conversation_id, text_message_content('Bounded proof turn'), {
              sender_label: 'Owner',
            });
            return { ok: true };
          },
        );
        app.post('/signals/:conversation_id/queue', async (request) => {
          for (let number = 1; number <= 2; number++) {
            await core.send(identity(request), text_message_content(`queued message ${number}`), {
              sender_label: 'owner',
              sender_message_id: `sender-${number}`,
            });
          }
          return { ok: true };
        });
        app.get('/signals/:conversation_id/answers', async (request) => {
          const snapshots = await registry.inspect(identity(request));
          const latest = snapshots.at(-1);
          if (!latest) throw new Error('No scripted child for the conversation');
          return Object.fromEntries(
            Object.entries(latest.answers).map(([request_id, answers]) => [
              request_id,
              Object.fromEntries(
                answers.map((answer) => [answer.question_id, { answers: answer.answers }]),
              ),
            ]),
          );
        });
        app.get('/signals/:conversation_id/delivered', async (request) => ({
          texts: (await registry.inspect(identity(request))).flatMap(
            (child) => child.delivered_text,
          ),
        }));
        app.post<{
          Body: { kind: 'ticket' | 'agent' | 'feedback'; id: string; conversation_id?: string };
        }>('/connected/bind', async (request) => {
          const { kind, id, conversation_id = '' } = request.body;
          context.database.write((tx) => {
            if (kind === 'ticket') {
              tx.run('UPDATE tickets SET conversation_id=? WHERE id=?', [conversation_id, id]);
              tx.run('INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)', [
                conversation_id,
                id,
              ]);
            } else if (kind === 'agent') {
              tx.run(
                'INSERT INTO agents(agent_key,conversation_id) VALUES (?,?) ON CONFLICT(agent_key) DO UPDATE SET conversation_id=excluded.conversation_id',
                [id, conversation_id],
              );
            } else {
              tx.run("UPDATE tickets SET stage='needs_feedback',pending_proposal=NULL WHERE id=?", [
                id,
              ]);
            }
          });
          return { ok: true };
        });
        app.post<{ Body: { kind: Principal['kind']; id: string; text: string } }>(
          '/connected/:conversation_id/message',
          async (request) => {
            const { kind, id, text } = request.body;
            await core.record_message_to_owner(identity(request), text_message_content(text), {
              sender_label: 'Proof Worker',
              sender: { kind, id },
              recipient: OWNER,
            });
            return { ok: true };
          },
        );
      },
    },
  ];
}
