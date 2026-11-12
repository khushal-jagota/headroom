/** Direct companion to worker_runtime_parity.py. Opens only its synthetic database copy. */
import { readFileSync } from 'node:fs';
import { createDatabase } from '../core/database/index.ts';
import { load_worker_runtime_definitions } from '../worker_types/configuration.ts';
import * as tickets from '../tickets/data/index.ts';
import * as readiness from '../runtime/worker_step_readiness.ts';
import { InMemoryConversationSystem } from '../conversation/in_memory_conversation_system.ts';
import { ConversationStartRequest, ConversationRoleMaterials } from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import * as start from '../runtime/conversation_start.ts';
import type { ConversationStartValues } from '../runtime/conversation_start.ts';
import type { Ticket } from '../tickets/contracts.ts';
import { PlannerError } from '../core/contracts.ts';

interface Input {
  mode: 'readiness' | 'abort';
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
}
const input = JSON.parse(readFileSync(process.argv[2], 'utf8')) as Input;
Date.now = () => input.wall;
const database = createDatabase(input.db_path);
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
try {
  let answer: Record<string, unknown>;
  if (input.mode === 'readiness') {
    const ticket =
      input.fieldless_snapshot ?? database.read((sql) => tickets.read_ticket(sql, input.ticket_id));
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
  process.stdout.write(JSON.stringify(answer));
} finally {
  database.close();
}
