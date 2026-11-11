import { homedir } from 'node:os';
import koffi from 'koffi';
import { PlannerError } from '../../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import type { CreateProjectBody, Project, UpdateProjectBody } from '../contracts.ts';
import { projectIdForName, strip } from '../logic/index.ts';

const libc = koffi.load(process.platform === 'darwin' ? '/usr/lib/libSystem.B.dylib' : 'libc.so.6');
const passwordEntry = koffi.struct('CatalogPasswordEntry', {
  pw_name: 'str', pw_passwd: 'str', pw_uid: 'uint', pw_gid: 'uint',
  ...(process.platform === 'darwin' ? { pw_change: 'long', pw_class: 'str' } : {}),
  pw_gecos: 'str', pw_dir: 'str', pw_shell: 'str',
});
const getPasswordEntry = libc.func('getpwnam', 'void *', ['str']);
function accountHome(name: string): string | undefined {
  const pointer = getPasswordEntry(name);
  return pointer === null ? undefined : (koffi.decode(pointer, passwordEntry) as { pw_dir: string }).pw_dir;
}

const COLUMNS = 'id, name, summary, folder_path, created_at, updated_at';
export function listProjects(sql: ReadSql): Project[] {
  return sql.all<Project>(`SELECT ${COLUMNS} FROM projects ORDER BY lower(name), id`);
}
export function readProject(sql: ReadSql, id: string): Project {
  const project = sql.get<Project>(`SELECT ${COLUMNS} FROM projects WHERE id = ?`, [id]);
  if (!project) throw new PlannerError('validation', 'invalid project_id', { project_id: id });
  return project;
}
export function resolveProject(sql: ReadSql, project_id: string | null, project: string | null, required = false): Project | null {
  const byId = project_id === null ? null : readProject(sql, project_id);
  let byName: Project | null = null;
  if (project !== null) {
    byName = sql.get<Project>(`SELECT ${COLUMNS} FROM projects WHERE name = ? COLLATE NOCASE`, [strip(project)]) ?? null;
    if (!byName) throw new PlannerError('validation', 'invalid project', { project });
  }
  if (byId && byName && byId.id !== byName.id) throw new PlannerError('validation', 'project_id and project do not match', { project_id, project });
  const result = byId ?? byName;
  if (!result && required) throw new PlannerError('validation', 'project is required');
  return result;
}

function normalizeFolderPath(raw: string | null): string | null {
  if (raw === null) return null;
  let value = raw;
  if (value.startsWith('~')) {
    const slash = value.indexOf('/');
    const name = value.slice(1, slash === -1 ? undefined : slash);
    let home: string | undefined;
    if (!name) home = process.env.HOME ?? homedir();
    else home = accountHome(name);
    if (!home) throw new PlannerError('validation', 'folder_path must be absolute or start with ~');
    value = home + (slash === -1 ? '' : value.slice(slash));
  }
  if (!value.startsWith('/')) throw new PlannerError('validation', 'folder_path must be absolute or start with ~');
  // PurePath removes redundant separators and dots but preserves '..' and leading '//'.
  const prefix = value.startsWith('//') && !value.startsWith('///') ? '//' : '/';
  return prefix + value.split('/').filter(part => part !== '' && part !== '.').join('/');
}
export function createProject(tx: TransactionSql, body: CreateProjectBody, now: number): Project {
  const name = strip(body.name), summary = strip(body.summary), folder_path = normalizeFolderPath(body.folder_path ?? null);
  if (!name) throw new PlannerError('validation', 'project name is required');
  if (tx.get('SELECT 1 FROM projects WHERE name = ? COLLATE NOCASE', [name])) throw new PlannerError('validation', 'project already exists', { name });
  const base = projectIdForName(name);
  let id = base, suffix = 2;
  while (tx.get('SELECT 1 FROM projects WHERE id = ?', [id])) id = `${base}_${suffix++}`;
  tx.run('INSERT INTO projects (id, name, summary, folder_path, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)', [id, name, summary, folder_path, now, now]);
  return readProject(tx, id);
}
export function updateProject(tx: TransactionSql, id: string, body: UpdateProjectBody, now: number): Project {
  const updates: Record<string, string | null> = {};
  if (body.name !== undefined) {
    const name = strip(body.name);
    if (!name) throw new PlannerError('validation', 'project name is required');
    updates.name = name;
  }
  if (body.summary !== undefined) updates.summary = strip(body.summary);
  if ('folder_path' in body) updates.folder_path = normalizeFolderPath(body.folder_path!);
  if (!Object.keys(updates).length) throw new PlannerError('validation', 'no project fields to update');
  readProject(tx, id);
  if ('name' in updates) {
    const existing = tx.get<{id: string}>('SELECT id FROM projects WHERE name = ? COLLATE NOCASE', [updates.name]);
    if (existing && existing.id !== id) throw new PlannerError('validation', 'project already exists', { name: updates.name });
  }
  tx.run(`UPDATE projects SET ${Object.keys(updates).map(key => `${key} = ?`).join(', ')}, updated_at = ? WHERE id = ?`, [...Object.values(updates), now, id]);
  return readProject(tx, id);
}
