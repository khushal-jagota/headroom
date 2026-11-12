import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import type { ReadSql, TransactionSql, Database } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { frontmatter_bounds, skill_description } from './logic.ts';
export const PACKAGED_SKILLS_ROOT = fileURLToPath(new URL('../../src/planner/skills/', import.meta.url));
export function has_skill(sql: ReadSql, name: string): boolean { return !!sql.get('SELECT 1 FROM managed_skills WHERE skill_name = ?', [name]); }
export function read_all_skill_sources(sql: ReadSql): Record<string, string> {
  return Object.fromEntries(sql.all<{ skill_name: string; source_text: string }>('SELECT skill_name, source_text FROM managed_skills ORDER BY skill_name').map(r => [r.skill_name, r.source_text]));
}
export function read_skill_source(sql: ReadSql, name: string): string {
  const row = sql.get<{ source_text: string }>('SELECT source_text FROM managed_skills WHERE skill_name = ?', [name]);
  if (!row) throw new PlannerError('not_found', 'skill not found', { skill_name: name });
  return row.source_text;
}
export function write_skill_source(tx: TransactionSql, name: string, source: string, options: { now: number; generated_from_workflow?: boolean }): void {
  skill_description(source, name);
  if (!options.generated_from_workflow && tx.get("SELECT 1 FROM sqlite_master WHERE type='table' AND name='worker_types'")) {
    for (const row of tx.all<{ definition_json: string }>('SELECT definition_json FROM worker_types ORDER BY position')) {
      const definition = JSON.parse(row.definition_json);
      if (definition.profile?.specialist_skill === name && 'guidance' in definition) {
        const previous = tx.get<{ source_text: string }>('SELECT source_text FROM managed_skills WHERE skill_name=?', [name]);
        if (!previous || frontmatter_bounds(previous.source_text)[1] !== frontmatter_bounds(source)[1]) throw new PlannerError('validation', 'edit specialist guidance through its structured Worker preset', { skill_name: name });
      }
    }
  }
  tx.run('INSERT INTO managed_skills (skill_name, source_text, updated_at) VALUES (?, ?, ?) ON CONFLICT(skill_name) DO UPDATE SET source_text = excluded.source_text, updated_at = excluded.updated_at', [name, source, options.now]);
}
export function delete_skill_source(tx: TransactionSql, name: string): void {
  read_skill_source(tx, name);
  if (['panels', 'panels-worker', 'panels-manager', 'panels-chief-of-staff'].includes(name)) throw new PlannerError('validation', 'a role skill cannot be deleted', { skill_name: name });
  for (const row of tx.all<{ worker_type: string; definition_json: string }>('SELECT worker_type, definition_json FROM worker_types ORDER BY position')) {
    if (JSON.parse(row.definition_json).profile?.specialist_skill === name) throw new PlannerError('validation', 'a Worker type uses this skill', { skill_name: name, worker_type: row.worker_type });
  }
  tx.run('DELETE FROM managed_skills WHERE skill_name = ?', [name]);
}
export function seed_packaged_skills(tx: TransactionSql): void {
  if (tx.get('SELECT 1 FROM managed_skills LIMIT 1')) return;
  for (const entry of readdirSync(PACKAGED_SKILLS_ROOT, { withFileTypes: true }).sort((a,b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
    const file = join(PACKAGED_SKILLS_ROOT, entry.name, 'SKILL.md');
    if (entry.isDirectory() && existsSync(file)) tx.run('INSERT INTO managed_skills (skill_name, source_text, updated_at) VALUES (?, ?, 0)', [entry.name, readFileSync(file, 'utf8')]);
  }
}
export function capture_skill_version(database: Database, name: string, content: Buffer): string {
  const sha = createHash('sha256').update(content).digest('hex');
  const id = `skill_version_${createHash('sha256').update(name).update('\0').update(sha).digest('hex')}`;
  return database.write(tx => {
    tx.run('INSERT INTO managed_skill_versions (id, skill_name, content_sha256, content) VALUES (?, ?, ?, ?) ON CONFLICT(skill_name, content_sha256) DO NOTHING', [id, name, sha, content]);
    const row = tx.get<{ id: string; content: Buffer }>('SELECT id, content FROM managed_skill_versions WHERE skill_name = ? AND content_sha256 = ?', [name, sha]);
    if (!row || !Buffer.from(row.content).equals(content)) throw new Error(`managed skill hash collision for ${name}`);
    return row.id;
  }, { changeSignal: 'silent' });
}
