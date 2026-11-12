import type { FastifyRequest } from 'fastify';
import type { Config } from '../core/config.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../resource_versions/contracts.ts';
import { agent_resolve_from_sql, NO_CONVERSATION_START_OVERRIDES } from '../runtime/conversation_start.ts';
import { read_chief_settings } from './service.ts';
let context: Pick<Config, 'db_path' | 'workspace_root'>;
export function configure_chief_resources(config: Config): void {
    context = { db_path: config.db_path, workspace_root: config.workspace_root };
}
function revision(sql: ReadSql, kind: string, id: string): string {
    return String(sql.get<{
        revision: number;
    }>('SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?', [kind, id])?.revision ?? 0);
}
export class ChiefResources implements ResourceProvider {
    capture(sql: ReadSql, _request: FastifyRequest, key: string): ResourceSnapshot | null {
        if (key === '/api/workers/chief-of-staff/settings') {
            return { revision: revision(sql, 'chief-settings', 'chief_of_staff') + ':' + revision(sql, 'skill-body', 'panels-chief-of-staff'),
                read: () => read_chief_settings(sql) };
        }
        if (key === '/api/chief/conversation/start-values') {
            const row = sql.get<{
                employee_backend: string;
                employee_launch_model: string;
            }>("SELECT employee_backend,employee_launch_model FROM chief_settings WHERE employee_id='chief_of_staff'");
            const model = row ? `${row.employee_backend}:${row.employee_launch_model}` : '';
            return { revision: revision(sql, 'chief-launch', 'chief_of_staff') + ':' + revision(sql, 'model-enablement', model), read: () => {
                    const values = agent_resolve_from_sql(sql, NO_CONVERSATION_START_OVERRIDES, context);
                    return {
                        backend_key: values.backend_key, model: values.model, reasoning_effort: values.reasoning_effort,
                        workspace_folder: values.workspace_path_policy === 'executor_default' ? null : values.workspace_folder,
                        workspace_path_policy: values.workspace_path_policy
                    };
                } };
        }
        return null;
    }
}
