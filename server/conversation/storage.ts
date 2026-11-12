/** Route-required durable identity helper. Conversation event storage belongs to Unit 14. */
import type { TransactionSql } from '../core/database/contracts.ts';
import type { ResolvedConversationStart } from './contracts.ts';
export function ensure_started_conversation_record(tx: TransactionSql, resolved: ResolvedConversationStart, options: {
    created_at: number;
}): void {
    const role = resolved.role_materials;
    const identity = JSON.stringify(role?.identity_environment_variables ?? []);
    tx.run('INSERT OR IGNORE INTO conversations (conversation_id,backend_key,model,reasoning_effort,workspace_folder,workspace_path_policy,role_text,identity_environment_variables,access,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', [
        resolved.conversation_id, resolved.backend_key, resolved.model, resolved.reasoning_effort, resolved.workspace_folder,
        resolved.workspace_path_policy, role?.role_text ?? null, identity, resolved.access, options.created_at,
    ]);
    const actual = tx.get<{
        backend_key: string;
        workspace_folder: string;
        workspace_path_policy: string;
        role_text: string | null;
        identity_environment_variables: string;
        access: string;
    }>('SELECT backend_key,workspace_folder,workspace_path_policy,role_text,identity_environment_variables,access FROM conversations WHERE conversation_id=?', [resolved.conversation_id]);
    if (!actual || actual.backend_key !== resolved.backend_key || actual.workspace_folder !== resolved.workspace_folder ||
        actual.workspace_path_policy !== resolved.workspace_path_policy || actual.role_text !== (role?.role_text ?? null) ||
        actual.identity_environment_variables !== identity || actual.access !== resolved.access) {
        throw new Error(`conversation ${resolved.conversation_id} started with values that differ from its durable record`);
    }
}
