import type { NavigationSource } from '../contracts.ts';
/** Every scoped view includes Chief, even before another navigation domain exists. */
export const chiefSource: NavigationSource = {
  kind: 'chief', roots: () => ['chief'],
  affected: (_sql, [kind, id]) => (kind === 'chief_settings' && id === 'chief_of_staff') || (kind === 'agents' && id === 'chief_of_staff') ? ['chief'] : [],
  derive(sql, id) {
    const row = sql.get<{ label: string }>('SELECT label FROM chief_settings WHERE employee_id=?', ['chief_of_staff']);
    const agent = sql.get<{ conversation_id: string | null }>('SELECT conversation_id FROM agents WHERE agent_key=?', ['chief_of_staff']);
    return { records: [{ kind: 'chief', id, value: { label: row?.label ?? 'Chief of Staff', conversation_id: agent?.conversation_id ?? null } }], dependencies: [['chief_settings', 'chief_of_staff'], ['agents', 'chief_of_staff']] };
  },
};
