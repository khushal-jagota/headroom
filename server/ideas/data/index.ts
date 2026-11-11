import { PlannerError } from '../../core/contracts.ts';
import type { ReadSql, TransactionSql } from '../../core/database/contracts.ts';
import { ID_PREFIXES, newId } from '../../core/ids.ts';
import type { CreateIdeaBody, Idea } from '../contracts.ts';
const COLUMNS = 'ideas.id, ideas.title, ideas.body, ideas.project_id, projects.name AS project, ideas.created_at, ideas.updated_at';
const JOIN = 'FROM ideas LEFT JOIN projects ON projects.id = ideas.project_id';
export function createIdea(tx: TransactionSql, body: CreateIdeaBody, now: number): Idea {
  if (!body.title) throw new PlannerError('validation', 'idea title is required');
  const id = newId(ID_PREFIXES.idea);
  tx.run('INSERT INTO ideas (id, title, body, project_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)', [id, body.title, body.body, body.project_id, now, now]);
  return tx.get<Idea>(`SELECT ${COLUMNS} ${JOIN} WHERE ideas.id = ?`, [id])!;
}
export function listIdeas(sql: ReadSql, project_id: string | null): Idea[] {
  return sql.all<Idea>(`SELECT ${COLUMNS} ${JOIN} ${project_id === null ? '' : 'WHERE ideas.project_id = ?'} ORDER BY ideas.created_at DESC, ideas.id`, project_id === null ? [] : [project_id]);
}
