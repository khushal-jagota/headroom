/** Conversation starts and automatic start admission. */
import type { StartRequest } from '../start_admission/contracts.ts';
import { ConversationState } from './state.ts';
import type { TurnToken } from '../backends/contracts.ts';
import type { ConversationStartRequest } from '../contracts.ts';
import { resolve_conversation_start_request } from '../logic/conversation_start_resolution.ts';
import type { SqliteProcessConversationSystem } from '../system.ts';

export async function automatic_start_request(
  host: SqliteProcessConversationSystem,
  identity: string,
  conversation_id: string,
): Promise<StartRequest> {
  host.require_runtime_ownership();
  return host.options.store.automatic_start_request(identity, conversation_id);
}

export function accept_automatic_start(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  turn: TurnToken,
): Promise<void> {
  return host.persist(state, () =>
    host.start_admission.accept(host.options.store.start_admission_database, turn),
  );
}

export function settle_automatic_ids(
  host: SqliteProcessConversationSystem,
  state: ConversationState,
  identities: Iterable<string | null>,
): Promise<void> {
  const keys = [...identities];
  if (!host.start_admission.has_requests(keys)) return Promise.resolve();
  return host.persist(state, () =>
    host.start_admission.settle_ids(host.options.store.start_admission_database, keys),
  );
}

export async function start_conversation(
  host: SqliteProcessConversationSystem,
  request: ConversationStartRequest,
): Promise<void> {
  return host.workspace_admission.operation(
    async (admitted) => {
      if (!admitted) throw host.workspace_admission.refusal();
      host.require_runtime_ownership();
      const refusal = host.execution_refusal(request.conversation_id);
      if (refusal !== null) throw refusal;
      const resolved = resolve_conversation_start_request(request);
      if (
        host.retiring_workspace_tickets.has(
          new Map(resolved.role_materials?.identity_environment_variables ?? []).get(
            'PLAN_TICKET_ID',
          ) ?? '',
        )
      )
        throw new Error('Ticket workspace admission is closed for retirement.');
      // The record is the first effect, before an in-memory identity can exist.
      const record = host.options.store.create_conversation(resolved, request.machine_id);
      if (!host.conversations.has(record.conversation_id)) {
        const state = new ConversationState(record);
        state.local_child_retirement_proven = true;
        state.custody_ready = Promise.resolve();
        host.conversations.set(record.conversation_id, state);
      }
    },
    { starts: true },
  );
}
