import type { NavigationSource } from '../contracts.ts';
export const projectsSource: NavigationSource = {
  kind: 'project',
  roots: sql => sql.all<{ id: string }>('SELECT id FROM projects ORDER BY id').map(row => row.id),
  affected: (_sql, [kind, id]) => kind === 'projects' ? [id] : [],
  derive(sql, id) {
    const row = sql.get<{ name: string }>('SELECT name FROM projects WHERE id=?', [id]);
    return { records: row ? [{ kind: 'project', id, value: { name: row.name } }] : [], dependencies: [['projects', id]] };
  },
};
