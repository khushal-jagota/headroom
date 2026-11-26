import {
  begin_recipient_lifecycle,
  finish_recipient_lifecycle,
} from '../message_delivery/data/lifecycles.ts';
import { release_admission_leases } from '../runtime/admission_locks.ts';
/** One explicit rule for an individual move and every member of a bulk move. */
import type { Database } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ConversationSystem } from '../conversation/contracts.ts';
import { PromptDeliveryQueued, PromptDeliveryRefused } from '../conversation/contracts.ts';
import { text_message_content } from '../conversation/message_content.ts';
import { require_usable_model } from '../conversation/backend_state.ts';
import { read_machine } from '../machines/data.ts';
import { machine_can_run, machine_driver_kinds } from '../executor/machine_drivers.ts';
import { read_presence } from '../executor/vps_presence_store.ts';
import {
  can_execute_machine,
  mac_is_present,
  machine_unavailable_reason,
} from '../tickets/locale_configuration.ts';
import * as conversations from '../runtime/conversation_start.ts';
import { read_ticket } from '../tickets/data/index.ts';
import { read_manager } from '../managers/data.ts';
import type { VpsExecutorRegistry } from '../executor/registry.ts';
import type {
  WorkMoveRequest,
  WorkMoveResult,
  WorkMoveBatchResult,
  WorkMovePhase,
} from './contracts.ts';
import {
  read_work,
  work_agent_key,
  write_work_destination,
  work_move_status,
  unfinished_work,
} from './data.ts';

export interface WorkMoveServices {
  database: Database;
  system: ConversationSystem;
  registry: Pick<VpsExecutorRegistry, 'work_move_source'>;
  now: () => number;
}
export async function move_work(
  services: WorkMoveServices,
  request: WorkMoveRequest,
  progress: (
    phase: WorkMovePhase,
    conversation_id?: string,
    delivery_pending?: boolean,
  ) => void = () => {},
): Promise<WorkMoveResult> {
  const { database, system, registry, now } = services;
  const { owner, machine_id } = request;
  let title = owner.kind === 'chief' ? 'Chief' : owner.id;
  let moved = false;
  try {
    return await conversations.run_recipient_lifecycle(
      owner.kind === 'ticket' ? `ticket:${owner.id}` : `agent:${work_agent_key(owner)}`,
      async () => {
        const previous = database.read((sql) => {
          const work = read_work(sql, owner);
          title = work.title;
          read_machine(sql, machine_id);
          if (work.machine_id === machine_id) return work;
          for (const selected of [machine_id, work.machine_id])
            if (!can_execute_machine(selected, sql) || !mac_is_present(sql, Date.now(), selected))
              throw new PlannerError('validation', machine_unavailable_reason(selected, sql));
          for (const selected of [machine_id, work.machine_id])
            if (read_presence(sql, selected)?.identity?.supports_work_moves !== true)
              throw new PlannerError(
                'validation',
                'Update source and destination machine code before a move.',
              );
          if (!machine_can_run(machine_driver_kinds(sql, machine_id), work.backend))
            throw new PlannerError('validation', 'The selected machine cannot run this backend.');
          if (work.model !== null) require_usable_model(sql, work.backend, work.model, machine_id);
          return work;
        });
        if (previous.machine_id === machine_id)
          return { owner, title, machine_id, moved: false, error: null };
        const lifecycle_target =
          owner.kind === 'chief' ? { kind: 'chief' as const, id: 'chief' } : owner;
        const generation = database.write((tx) => begin_recipient_lifecycle(tx, lifecycle_target));
        release_admission_leases();
        // The link remains current until both exact process cleanup and the Git proof succeed.
        progress('stopping');
        if (previous.conversation_id !== null) await system.kill(previous.conversation_id);
        progress('verifying');
        const proof = await registry.work_move_source(previous.machine_id, owner);
        progress('recording');
        database.write((tx) => {
          write_work_destination(tx, owner, previous, machine_id, proof, now());
          finish_recipient_lifecycle(tx, lifecycle_target, generation);
        });
        moved = true;
        progress('recovering');
        const record = database.read((sql) => read_work(sql, owner).record);
        const prompt =
          'The owner moved your work to this machine. This is a fresh conversation. ' +
          'Recover your context from the following canonical record and read your Panels skill layers. ' +
          (owner.kind === 'ticket'
            ? 'Read your full Ticket record and workflow, including settled stage text, the pending proposal, recap and relevant history. Preserve every approval gate and backlog pause. This message only restores context. Wait for the next authorized Worker step before substantive work. '
            : owner.kind === 'manager'
              ? 'Read your Manager page, goal, recap and current child Tickets through Panels before you act. Continue only work that the record authorizes. '
              : 'Read your current Chief page, recap and Manager and Ticket records through Panels before you act. Continue only work that the record authorizes. ') +
          '\n\nCanonical record:\n' +
          JSON.stringify(record);
        if (owner.kind === 'ticket') {
          const ticket = database.read((sql) => read_ticket(sql, owner.id));
          const linked = await conversations.start_ticket_conversation(
            system,
            database,
            ticket,
            conversations.worker_resolve(database, ticket),
            { conversation_id: conversations.new_conversation_id(), now: now() },
          );
          progress('recovering', linked.conversation_id);
          const fate = await system.send(linked.conversation_id, text_message_content(prompt), {
            sender_label: 'machine move',
            sender_message_id: `work-move:${conversations.new_conversation_id()}`,
            mode: 'queue',
          });
          if (fate instanceof PromptDeliveryQueued)
            progress('queued', linked.conversation_id, true);
          if (fate instanceof PromptDeliveryRefused)
            throw new PlannerError(
              'validation',
              `The work moved, but its new conversation refused the recovery prompt: ${fate.refusal_reason}`,
            );
        } else {
          const values =
            owner.kind === 'chief'
              ? conversations.agent_resolve(database)
              : conversations.manager_resolve(
                  database,
                  database.read((sql) => read_manager(sql, owner.id).item),
                );
          const created_conversation_id = conversations.new_conversation_id();
          progress('recovering', created_conversation_id);
          const delivered = await conversations.send_to_agent_conversation(
            system,
            database,
            work_agent_key(owner),
            text_message_content(prompt),
            values,
            {
              conversation_id: null,
              created_conversation_id,
              sender_label: 'machine move',
              sender_message_id: `work-move:${conversations.new_conversation_id()}`,
              mode: 'queue',
              required_manager_id: owner.kind === 'manager' ? owner.id : null,
              retain_created_conversation: true,
            },
          );
          if (delivered.fate instanceof PromptDeliveryQueued && delivered.conversation_id !== null)
            progress('queued', delivered.conversation_id, true);
          if (delivered.fate instanceof PromptDeliveryRefused)
            throw new PlannerError(
              'validation',
              `The work moved, but its new conversation refused the recovery prompt: ${delivered.fate.refusal_reason}`,
            );
        }
        return { owner, title, machine_id, moved: true, error: null };
      },
    );
  } catch (error) {
    return {
      owner,
      title,
      machine_id,
      moved,
      error: error instanceof Error ? error.message : 'The move failed.',
    };
  }
}
export async function move_everything(
  services: WorkMoveServices,
  machine_id: string,
): Promise<WorkMoveBatchResult> {
  const owners = services.database.read((sql) => {
    if (work_move_status(sql).move_everything_machine_id !== machine_id)
      throw new PlannerError(
        'validation',
        'Move everything is available on the new server machine after a successful server move.',
      );
    return unfinished_work(sql);
  });
  const results: WorkMoveResult[] = [];
  for (const owner of owners) results.push(await move_work(services, { owner, machine_id }));
  return { results };
}
