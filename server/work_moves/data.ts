/** Destination writes preserve the canonical work record and its review gates. */
import type { ReadSql, TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { HOME_MACHINE_ID } from '../execution_identity.ts';
import { read_ticket, release_worker_step_claim } from '../tickets/data/index.ts';
import { capture_ticket_attention } from '../notifications/attention.ts';
import { read_manager } from '../managers/data.ts';
import { read_chief_settings, CHIEF_SETTINGS_KEY } from '../worker_settings/service.ts';
import { agent_machine_id } from '../machines/conversation_machine.ts';
import { read_server_location } from '../server_link/data.ts';
import { parse_backend_instance_id } from '../drivers/core/identity.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import type { WorkMoveOwner, WorkMoveProof, WorkMoveStatus } from './contracts.ts';

export function work_agent_key(owner: WorkMoveOwner): string {
  return owner.kind === 'chief'
    ? CHIEF_SETTINGS_KEY
    : `manager_${owner.kind === 'manager' ? owner.id : ''}`;
}
export function read_work(sql: ReadSql, owner: WorkMoveOwner) {
  if (owner.kind === 'ticket') {
    const ticket = read_ticket(sql, owner.id);
    const defaults =
      ticket.employee_launch_model === null && ticket.worker_type !== null
        ? configured_worker_type_registry().require(ticket.worker_type).worker_profile
        : null;
    return {
      title: ticket.title,
      machine_id: ticket.machine_id,
      conversation_id: ticket.conversation_id,
      backend: parse_backend_instance_id(ticket.employee_backend),
      model: ticket.employee_launch_model ?? defaults?.default_model ?? null,
      record: ticket,
    };
  }
  const agent_key = work_agent_key(owner);
  const conversation_id =
    sql.get<{ conversation_id: string | null }>(
      'SELECT conversation_id FROM agents WHERE agent_key=?',
      [agent_key],
    )?.conversation_id ?? null;
  if (owner.kind === 'manager') {
    const manager = read_manager(sql, owner.id).item;
    return {
      title: manager.title,
      machine_id: agent_machine_id(sql, owner),
      conversation_id,
      backend: manager.manager_launch_configuration.employee_backend,
      model: manager.manager_launch_configuration.employee_launch_model,
      record: manager,
    };
  }
  const chief = read_chief_settings(sql);
  return {
    title: chief.label,
    machine_id: agent_machine_id(sql, owner),
    conversation_id,
    backend: parse_backend_instance_id(chief.launch_defaults.employee_backend),
    model: chief.launch_defaults.employee_launch_model,
    record: { label: chief.label, recap: chief.recap, launch_defaults: chief.launch_defaults },
  };
}
export function write_work_destination(
  tx: TransactionSql,
  owner: WorkMoveOwner,
  expected: { machine_id: string; conversation_id: string | null },
  machine_id: string,
  proof: WorkMoveProof,
  now: number,
): void {
  const current = read_work(tx, owner);
  if (
    current.machine_id !== expected.machine_id ||
    current.conversation_id !== expected.conversation_id
  )
    throw new PlannerError(
      'validation',
      'The work changed while the source machine checked it. Retry the move.',
    );
  if (current.conversation_id !== null)
    tx.run('DELETE FROM agent_launch_tokens WHERE conversation_id=?', [current.conversation_id]);
  if (owner.kind === 'ticket') {
    const ticket = read_ticket(tx, owner.id);
    // A failed destination setup can leave no checkout. Keep the last published work in that case.
    const retained = tx.get<WorkMoveProof>(
      'SELECT published_branch,published_revision FROM tickets WHERE id=?',
      [owner.id],
    );
    const published =
      proof.published_revision === null && retained?.published_revision != null ? retained : proof;
    tx.run(
      'UPDATE tickets SET machine_id=?,locale=?,conversation_id=NULL,published_branch=?,published_revision=?,updated_at=? WHERE id=?',
      [
        machine_id,
        machine_id === HOME_MACHINE_ID ? 'vps' : 'macos',
        published.published_branch,
        published.published_revision,
        now,
        owner.id,
      ],
    );
    if (ticket.worker_step_claim === 'out')
      release_worker_step_claim(tx, owner.id, {
        expected_claim: 'out',
        expected_claim_revision: ticket.worker_step_claim_revision,
        now,
      });
    capture_ticket_attention(tx, owner.id, now);
  } else {
    tx.run(
      'INSERT INTO agents(agent_key,conversation_id,machine_id) VALUES (?,NULL,?) ON CONFLICT(agent_key) DO UPDATE SET conversation_id=NULL,machine_id=excluded.machine_id',
      [work_agent_key(owner), machine_id],
    );
  }
}
export function work_move_status(sql: ReadSql): WorkMoveStatus {
  const location = read_server_location(sql);
  const home = sql.get<{ machine_id: string }>('SELECT machine_id FROM machines WHERE is_home=1');
  return {
    move_everything_machine_id:
      location?.state === 'active' && location.move_number > 0 ? (home?.machine_id ?? null) : null,
  };
}
export function unfinished_work(sql: ReadSql): WorkMoveOwner[] {
  return [
    ...sql
      .all<{ id: string }>(
        "SELECT id FROM tickets WHERE NOT EXISTS (SELECT 1 FROM json_each(workflow_json,'$.stages') s WHERE json_extract(s.value,'$.id')=stage AND json_extract(s.value,'$.is_terminal')) ORDER BY id",
      )
      .map(({ id }) => ({ kind: 'ticket' as const, id })),
    ...sql
      .all<{ id: string }>('SELECT id FROM managers WHERE settled=0 ORDER BY id')
      .map(({ id }) => ({ kind: 'manager' as const, id })),
    { kind: 'chief' as const },
  ];
}
