import { realpathSync, rmSync } from 'node:fs';
import { dirname, isAbsolute, join, relative } from 'node:path';
import type { Database, ReadSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import type { ManagedSkill, SkillOptions, SkillSaveIncompleteDetail } from './contracts.ts';
import {
  delete_skill_source,
  has_skill,
  read_all_skill_sources,
  read_skill_source,
  write_skill_source,
  capture_skill_version,
} from './data.ts';
import {
  parse_skill,
  normalize_skill_patch,
  render_new_skill,
  render_skill_from_existing_frontmatter,
} from './logic.ts';
import { with_skill_publication_lock, atomic_replace_text } from './files.ts';
export { parse_skill, normalize_skill_patch } from './logic.ts';
export { with_skill_publication_lock } from './files.ts';
export function read_skill(sql: ReadSql, name: string): ManagedSkill {
  return parse_skill(read_skill_source(sql, name), name);
}
export function read_skills_home(sql: ReadSql): { skills: ManagedSkill[] } {
  return {
    skills: Object.entries(read_all_skill_sources(sql)).map(([name, source]) =>
      parse_skill(source, name),
    ),
  };
}
export function render_skill_patch(
  sql: ReadSql,
  name: string,
  raw: Record<string, unknown>,
  allowCreate = false,
): string {
  const patch = normalize_skill_patch(name, raw);
  if (allowCreate && !has_skill(sql, name)) {
    for (const field of ['description', 'markdown_body'] as const)
      if (!(field in patch))
        throw new PlannerError('validation', `${field} is required for a new skill`, { field });
    return render_new_skill(name, patch.description!, patch.markdown_body!);
  }
  const current = read_skill(sql, name);
  return render_skill_from_existing_frontmatter(current.source_text, {
    expected_skill_name: name,
    description: patch.description ?? current.description,
    markdown_body: patch.markdown_body ?? current.markdown_body,
  });
}
export function publish_skill(
  database: Database,
  root: string,
  name: string,
  source: string,
): void {
  const detail: SkillSaveIncompleteDetail = {
    skill_name: name,
    saved: true,
    published: false,
    phase: 'file',
  };
  try {
    atomic_replace_text(join(root, name, 'SKILL.md'), source);
  } catch (cause) {
    throw new PlannerError(
      'skill_save_incomplete',
      'The skill was saved, but its file was not published. Read the canonical skill before retry.',
      { ...detail },
    );
  }
  detail.published = true;
  detail.phase = 'history';
  try {
    capture_skill_version(database, name, Buffer.from(source));
  } catch (cause) {
    throw new PlannerError(
      'skill_save_incomplete',
      'The skill was saved and published, but version history was not recorded. Read the canonical skill before retry.',
      { ...detail },
    );
  }
}
export function save_skill(
  database: Database,
  name: string,
  raw: Record<string, unknown>,
  options: SkillOptions,
): ManagedSkill {
  normalize_skill_patch(name, raw);
  return with_skill_publication_lock(options.database_parent, (root) => {
    const result = database.write((tx) => {
      const source = render_skill_patch(tx, name, raw, options.allow_create);
      const skill = parse_skill(source, name);
      write_skill_source(tx, name, source, options);
      return skill;
    });
    publish_skill(database, root, name, result.source_text);
    return result;
  });
}
export function apply_skills(
  database: Database,
  raw: Record<string, unknown>,
  options: SkillOptions,
): ManagedSkill[] {
  if (Object.keys(raw).length !== 1 || !Array.isArray(raw.skills) || !raw.skills.length)
    throw new PlannerError('validation', 'skills must be a nonempty array');
  const names = new Set<string>();
  const entries = raw.skills.map((entry: unknown) => {
    if (
      !entry ||
      typeof entry !== 'object' ||
      Array.isArray(entry) ||
      Object.keys(entry).sort().join(',') !== 'name,source_text'
    )
      throw new PlannerError('validation', 'skill application must be an exact object');
    const { name, source_text } = entry as Record<string, unknown>;
    if (typeof name !== 'string' || !/^[a-z0-9][a-z0-9_-]*$/.test(name) || names.has(name))
      throw new PlannerError('validation', 'skill name is invalid or duplicated');
    if (typeof source_text !== 'string')
      throw new PlannerError('validation', 'source text is invalid');
    names.add(name);
    return parse_skill(source_text, name);
  });
  return with_skill_publication_lock(options.database_parent, (root) => {
    database.write((tx) => {
      const current = read_all_skill_sources(tx);
      if (!entries.every((entry) => current[entry.name] === entry.source_text))
        for (const entry of entries) write_skill_source(tx, entry.name, entry.source_text, options);
    });
    for (const entry of entries) publish_skill(database, root, entry.name, entry.source_text);
    return entries;
  });
}
export function delete_skill(
  database: Database,
  name: string,
  options: { database_parent: string },
): void {
  with_skill_publication_lock(options.database_parent, (root) =>
    database.write((tx) => {
      delete_skill_source(tx, name);
      if (!name || name === '.' || name === '..' || name.includes('/'))
        throw new PlannerError('validation', 'skill name is invalid', { skill_name: name });
      rmSync(join(root, name), { recursive: true, force: true });
    }),
  );
}
function realExisting(path: string): string {
  try {
    return realpathSync(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    const parent = dirname(path);
    if (parent === path) throw error;
    return join(realExisting(parent), path.slice(parent.length + (parent === '/' ? 0 : 1)));
  }
}
export function write_skill_file(
  database: Database,
  name: string,
  raw: Record<string, unknown>,
  options: { database_parent: string },
): string {
  if (Object.keys(raw).sort().join(',') !== 'content,path')
    throw new PlannerError('validation', 'send exactly path and content');
  if (typeof raw.content !== 'string' || !raw.content.trim())
    throw new PlannerError('validation', 'content must not be blank');
  const path = raw.path;
  const parts =
    typeof path === 'string' ? path.split('/').filter((part) => part && part !== '.') : [];
  if (
    !parts.length ||
    typeof path !== 'string' ||
    path.startsWith('/') ||
    path.includes('\\') ||
    parts.some((p) => p === '..')
  )
    throw new PlannerError('validation', 'path must be relative and inside the skill folder');
  if (parts.length === 1 && parts[0] === 'SKILL.md')
    throw new PlannerError('validation', 'edit SKILL.md through panels skill edit', { path });
  const content = raw.content;
  with_skill_publication_lock(options.database_parent, (root) => {
    database.read((sql) => read_skill_source(sql, name));
    const folder = realExisting(join(root, name)),
      target = join(folder, ...parts),
      resolved = realExisting(target),
      rel = relative(folder, resolved);
    if (rel.startsWith('../') || isAbsolute(rel) || resolved === join(folder, 'SKILL.md'))
      throw new PlannerError('validation', 'path must be relative and inside the skill folder');
    atomic_replace_text(target, content);
  });
  return parts.join('/');
}
