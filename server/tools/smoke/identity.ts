/** The private smoke fixture gives its Worker a real canonical conversation credential. */
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import type { Database } from '../../core/database/contracts.ts';
import { ResolvedConversationStart } from '../../conversation/contracts.ts';
import { ensure_started_conversation_record } from '../../conversation/record/data/store.ts';
import { write_ticket_conversation_start } from '../../tickets/data/index.ts';
import { issue_launch_token } from '../../agent_identity/data.ts';

export function smoke_worker_token(
  database: Database,
  ticket_id: string,
  options: { model: string; workspace: string },
): string {
  const conversation_id = 'smoke-worker-' + randomUUID();
  const now = Math.floor(Date.now() / 1000);
  database.write((tx) => {
    ensure_started_conversation_record(
      tx,
      new ResolvedConversationStart({
        conversation_id,
        backend_key: 'codex',
        model: options.model,
        reasoning_effort: null,
        role_materials: null,
        workspace_folder: options.workspace,
        access: 'full',
      }),
      { created_at: now },
    );
    const ticket = write_ticket_conversation_start(tx, ticket_id, {
      conversation_id,
      backend: 'codex',
      model: options.model,
      reasoning_effort: null,
      now,
    });
    assert.equal(ticket.conversation_id, conversation_id);
  });
  return issue_launch_token(database, conversation_id);
}
