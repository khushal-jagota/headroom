/** Apply exactly the floor defaults from the conversation contract. */
import { isAbsolute } from 'node:path';
import { ConversationRoleMaterials, ResolvedConversationStart, FLOOR_DEFAULT_ACCESS, FLOOR_DEFAULT_BACKEND_KEY, FLOOR_DEFAULT_WORKSPACE_FOLDER } from '../contracts.ts';
import type { ConversationStartRequest } from '../contracts.ts';

export function bind_conversation_origin(materials: ConversationRoleMaterials | null, conversation_id: string): ConversationRoleMaterials | null {
    if (materials === null) return null;
    return new ConversationRoleMaterials({
        ...materials,
        identity_environment_variables: [
            ...materials.identity_environment_variables.filter(([name]) => name !== 'PLAN_CONVERSATION_ID'),
            ['PLAN_CONVERSATION_ID', conversation_id],
        ],
    });
}
export function resolve_conversation_start_request(request: ConversationStartRequest): ResolvedConversationStart {
    if (!request.conversation_id || request.conversation_id !== request.conversation_id.trim()) throw new Error('conversation id must be non-empty and trimmed');
    if (!request.model || request.model !== request.model.trim()) throw new Error('model must be non-empty and trimmed');
    const workspace_folder = request.workspace_folder ?? FLOOR_DEFAULT_WORKSPACE_FOLDER;
    if (!isAbsolute(workspace_folder)) throw new Error('workspace folder must be an absolute path');
    return new ResolvedConversationStart({
        conversation_id: request.conversation_id,
        backend_key: request.backend_key ?? FLOOR_DEFAULT_BACKEND_KEY,
        model: request.model,
        reasoning_effort: request.reasoning_effort ?? null,
        role_materials: bind_conversation_origin(request.role_materials ?? null, request.conversation_id),
        workspace_folder,
        workspace_path_policy: request.workspace_path_policy ?? 'home_relative',
        access: request.access ?? FLOOR_DEFAULT_ACCESS,
    });
}
