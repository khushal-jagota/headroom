/** Export-only companion for the direct Worker comparison in tests/compare/direct/. */
import { createDatabase } from '../core/database/index.ts';
import { load_worker_runtime_definitions } from '../worker_types/configuration.ts';
import * as tickets from '../tickets/data/index.ts';
import * as decisions from '../tickets/decision_messages.ts';
import { OWNER } from '../core/contracts.ts';
import * as readiness from '../runtime/worker_step_readiness.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import { ConversationStartRequest, ConversationRoleMaterials } from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import * as start from '../runtime/conversation_start.ts';
import type { ConversationStartValues } from '../runtime/conversation_start.ts';
import type { Ticket } from '../tickets/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { PROTOCOL_VERSION } from '../execution_configuration.ts';
import { ORIGINAL_MAC_MACHINE_ID } from '../execution_identity.ts';
import * as machine_admission from '../tickets/locale_configuration.ts';
import { require_restartable } from '../tickets/worker_restart.ts';
import type { ExecutorIdentity } from '../execution_identity.ts';

export interface WorkerRuntimeParityInput {
  mode: 'readiness' | 'abort' | 'decisions' | 'machine_admission';
  db_path: string;
  workspace: string;
  now: number;
  wall: number;
  ticket_id: string;
  fieldless_snapshot?: Ticket;
  kind?: string;
  agent_key?: string;
  manager_id?: string;
  existing?: boolean;
  values?: ConversationStartValues;
  machine_tickets?: Record<string, string>;
  named_machines?: readonly [string, string];
  source_identity?: ExecutorIdentity;
  candidate_identity?: ExecutorIdentity;
}
export async function run_worker_runtime_parity(
  input: WorkerRuntimeParityInput,
): Promise<Record<string, unknown>> {
  const database = createDatabase(input.db_path);
  const original_now = Date.now;
  Date.now = () => input.wall;
  try {
    start.configure_conversation_start(database, {
      db_path: input.db_path,
      workspace_root: input.workspace,
    });
    database.read(load_worker_runtime_definitions);
    function rows(): Record<string, unknown> {
      return database.read((sql) =>
        Object.fromEntries(
          [
            'tickets',
            'ticket_paired_stage_openers',
            'ticket_conversations',
            'agents',
            'conversations',
          ].map((table) => [table, sql.all(`SELECT * FROM ${table} ORDER BY 1,2`)]),
        ),
      );
    }
    let answer: Record<string, unknown>;
    if (input.mode === 'machine_admission') {
      const machine_tickets = input.machine_tickets!;
      const [machine_a, machine_b] = input.named_machines!;
      const identity = input.candidate_identity!;
      if (identity.protocol_version !== PROTOCOL_VERSION)
        throw new Error("The candidate fixture must use this runtime's supported identity");
      database.write((sql) =>
        sql.run('UPDATE mac_executor_presence SET identity_json=? WHERE machine_id=?', [
          JSON.stringify(identity),
          machine_a,
        ]),
      );
      const system = new InMemoryConversationSystem();
      const capture = async () => {
        const result: Record<string, unknown> = {};
        for (const [name, id] of Object.entries(machine_tickets)) {
          const ticket = database.read((sql) => tickets.read_ticket(sql, id));
          const admitted = database.read((sql) =>
            machine_admission.can_execute_machine(ticket.machine_id, sql),
          );
          let restart: unknown = null,
            send: unknown = null;
          try {
            database.read((sql) => require_restartable(sql, OWNER, id));
          } catch (error) {
            if (!(error instanceof PlannerError)) throw error;
            restart = error.toPayload();
          }
          if (!admitted) {
            try {
              await start.send_to_ticket_conversation(
                system,
                database,
                id,
                text_message_content('Refused machine opener'),
                {
                  now: input.now,
                  conversation_id: null,
                  sender_label: 'test',
                },
              );
              throw new Error('A refused destination started a conversation');
            } catch (error) {
              if (!(error instanceof PlannerError)) throw error;
              send = error.toPayload();
            }
          }
          result[name] = database.read((sql) => ({
            admitted,
            reason: machine_admission.machine_unavailable_reason(ticket.machine_id, sql),
            readiness: readiness.worker_step_blocker(sql, ticket, {
              worker_type_definition: ticket.workflow,
            }),
            restart,
            send,
          }));
        }
        return result;
      };
      const phases = [await capture()];
      database.write((sql) => {
        sql.run('UPDATE mac_executor_presence SET last_lease_at_ms=? WHERE machine_id=?', [
          input.wall - 60000,
          machine_a,
        ]);
        const other_identity = {
          ...identity,
          executor_incarnation: '88888888-8888-4888-8888-888888888888',
        };
        sql.run(
          'INSERT INTO mac_executor_presence(machine_id,executor_incarnation,identity_json,last_contact_at_ms,last_lease_at_ms,continuous_since_ms) VALUES(?,?,?,?,?,?)',
          [
            machine_b,
            other_identity.executor_incarnation,
            JSON.stringify(other_identity),
            input.wall,
            input.wall,
            input.wall,
          ],
        );
        sql.run('UPDATE machines SET name=? WHERE machine_id=?', [
          'Café $&',
          ORIGINAL_MAC_MACHINE_ID,
        ]);
      });
      phases.push(await capture());
      let unknown: unknown = null;
      try {
        database.read((sql) => machine_admission.machine_unavailable_reason('unknown', sql));
      } catch (error) {
        if (!(error instanceof PlannerError)) throw error;
        unknown = error.toPayload();
      }
      answer = {
        fixture: { source_identity: input.source_identity, candidate_identity: identity },
        phases,
        unknown,
        rows: rows(),
      };
    } else if (input.mode === 'decisions') {
      const ticket = database.read((sql) => tickets.read_ticket(sql, input.ticket_id));
      const options = {
        message: 'Keep the attributed note.',
        principal: OWNER,
        now: input.now,
        expected_proposal_revision: ticket.pending_proposal_revision,
        decision_id: 'parity-decision',
      };
      database.write((sql) => tickets.reject_proposal(sql, ticket.id, options));
      database.write((sql) => tickets.reject_proposal(sql, ticket.id, options));
      const revised = database.write((sql) =>
        tickets.file_current_proposal(sql, ticket.id, {
          body: 'Reviewed scope',
          principal: { kind: 'ticket', id: ticket.id },
          now: input.now,
        }),
      );
      database.write((sql) =>
        tickets.accept_proposal(sql, ticket.id, 'what_changes', {
          principal: OWNER,
          now: input.now,
          next_ceiling: 'needs_implementation',
          next_holder: OWNER,
          expected_proposal_revision: revised.pending_proposal_revision,
          message: 'Approuvé — café et 日本語.',
          decision_id: 'parity-approval',
        }),
      );
      database.write((sql) => {
        sql.run(
          "UPDATE ticket_decision_messages SET delivery_status='uncertain' WHERE ticket_id=?",
          [ticket.id],
        );
        decisions.rearm_after_restart(sql, ticket.id);
      });
      answer = database.read((sql) => ({
        rows: sql.all('SELECT * FROM ticket_decision_messages ORDER BY decision_id'),
        pending: decisions.pending_ticket_ids(sql),
        undelivered: decisions.has_undelivered(sql, ticket.id),
      }));
    } else if (input.mode === 'readiness') {
      const ticket =
        input.fieldless_snapshot ??
        database.read((sql) => tickets.read_ticket(sql, input.ticket_id));
      const reason = database.read((sql) =>
        readiness.worker_step_blocker(sql, ticket, { worker_type_definition: ticket.workflow }),
      );
      let claimed = false;
      let error: unknown = null;
      try {
        claimed =
          database.write((tx) =>
            tickets.claim_ticket_for_worker_step(tx, input.ticket_id, {
              readiness_check: readiness.is_ready_for_worker_step,
              now: input.now,
            }),
          ) !== null;
      } catch (caught) {
        if (!(caught instanceof PlannerError)) throw caught;
        error = { code: caught.code, message: caught.message, detail: caught.detail };
      }
      answer = { ready: reason === null, reason, claimed, error, rows: rows() };
    } else {
      const system = new InMemoryConversationSystem();
      const controller = new AbortController();
      let entered!: () => void;
      const sending = new Promise<void>((resolve) => {
        entered = resolve;
      });
      const killed: string[] = [];
      const kill = system.kill.bind(system);
      system.kill = async (id) => {
        killed.push(id);
        await kill(id);
      };
      system.send = () => {
        entered();
        return new Promise(() => {});
      };
      const values =
        input.values === undefined
          ? null
          : {
              ...input.values,
              role_materials: new ConversationRoleMaterials(input.values.role_materials),
            };
      if (input.existing) {
        const record = database.read((sql) =>
          sql.get<{ backend_key: 'codex'; model: string; workspace_folder: string }>(
            'SELECT backend_key,model,workspace_folder FROM conversations WHERE conversation_id=?',
            ['parity_conversation'],
          ),
        );
        await system.start_conversation(
          new ConversationStartRequest({
            conversation_id: 'parity_conversation',
            backend_key: record?.backend_key ?? 'codex',
            model: record?.model ?? 'gpt-6.1-sol',
            workspace_folder: record?.workspace_folder ?? input.workspace,
          }),
        );
      }
      const options = {
        conversation_id: input.existing ? 'parity_conversation' : null,
        created_conversation_id: 'parity_conversation',
        sender_label: 'test',
        signal: controller.signal,
      };
      const task =
        input.kind === 'ticket'
          ? start.send_to_ticket_conversation(
              system,
              database,
              input.ticket_id,
              text_message_content('parity opener'),
              { ...options, now: input.now },
            )
          : start.send_to_agent_conversation(
              system,
              database,
              input.agent_key!,
              text_message_content('parity opener'),
              values,
              { ...options, required_manager_id: input.manager_id },
            );
      await sending;
      controller.abort();
      let aborted = false;
      try {
        await task;
      } catch (error) {
        if (!(error instanceof Error) || error.name !== 'AbortError') throw error;
        aborted = true;
      }
      answer = { aborted, killed, rows: rows() };
    }
    return answer;
  } finally {
    database.close();
    Date.now = original_now;
  }
}
