import { mkdtempSync, mkdirSync, readFileSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, expect, test } from 'vitest';
import { createDatabase } from '../core/database/index.ts';
import { PlannerError } from '../core/contracts.ts';
import { provision_native_backend_skills, provision_workspace_skills } from './sources.ts';
import { read_skill, save_skill } from './service.ts';
import { render_new_skill } from './logic.ts';
import { save_worker_type_definition } from '../worker_settings/service.ts';
import { definition_from_json } from '../worker_types/store.ts';
import { shipped_worker_types } from '../worker_types/shipped.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
const cleanup: (() => void)[] = [];
afterEach(() => { for (const action of cleanup.splice(0).reverse()) action(); });
function root(): string { const path = mkdtempSync(join(tmpdir(), 'panels-skills-')); cleanup.push(() => rmSync(path, { recursive: true, force: true })); return path; }

test('synthetic skill links refuse a symlink ancestor outside the owned home before any mutation', () => {
  const path = root(), home = join(path, 'home'), foreign = join(path, 'foreign'), data = join(path, 'data');
  mkdirSync(home); mkdirSync(foreign); mkdirSync(join(data, 'skills', 'example'), { recursive: true });
  writeFileSync(join(data, 'skills', 'example', 'SKILL.md'), 'Example');
  symlinkSync(foreign, join(home, '.codex'), 'dir');
  expect(() => provision_native_backend_skills(join(home, '.codex'), data, { test_home: home })).toThrow('synthetic destination escapes owned home');
  expect(readdirSync(foreign)).toEqual([]);
  expect(readdirSync(home)).toEqual(['.codex']);
  const workspace = join(home, 'workspace'); mkdirSync(workspace);
  symlinkSync(foreign, join(workspace, '.agents'), 'dir');
  expect(() => provision_workspace_skills(workspace, data)).toThrow('workspace skills conflict');
  expect(readdirSync(foreign)).toEqual([]);
  rmSync(join(workspace, '.agents')); mkdirSync(join(workspace, '.agents'));
  symlinkSync(foreign, join(workspace, '.agents', 'skills'), 'dir');
  expect(() => provision_workspace_skills(workspace, data)).toThrow('workspace skills conflict');
  expect(readdirSync(foreign)).toEqual([]);
  rmSync(join(workspace, '.agents', 'skills'));
  const projected = provision_workspace_skills(workspace, data);
  expect(realpathSync(join(workspace, '.agents', 'skills'))).toBe(projected);
  expect(provision_workspace_skills(workspace, data)).toBe(projected);
});

test('incomplete publication preserves the committed source and identifies file or history failure', () => {
  const path = root(), file = join(path, 'db.sqlite3'); writeFileSync(file, '');
  const database = createDatabase(file); cleanup.push(() => database.close());
  database.write(tx => {
    tx.run('CREATE TABLE managed_skills (skill_name TEXT PRIMARY KEY, source_text TEXT, updated_at INTEGER)');
    tx.run('CREATE TABLE worker_types (worker_type TEXT PRIMARY KEY, definition_json TEXT, position INTEGER, updated_at INTEGER)');
    tx.run('CREATE TABLE managed_skill_versions (id TEXT PRIMARY KEY, skill_name TEXT, content_sha256 TEXT, content BLOB, UNIQUE(skill_name, content_sha256))');
    tx.run('INSERT INTO managed_skills VALUES (?, ?, 0)', ['example', render_new_skill('example', 'Original', 'Body')]);
  });
  const target = join(path, 'skills', 'example', 'SKILL.md'); mkdirSync(target, { recursive: true });
  let error: unknown;
  try { save_skill(database, 'example', { description: 'Saved edit' }, { now: 1, database_parent: path }); } catch (e) { error = e; }
  expect(error).toBeInstanceOf(PlannerError);
  expect((error as PlannerError).detail).toEqual({ skill_name: 'example', saved: true, published: false, phase: 'file' });
  expect(database.read(sql => read_skill(sql, 'example')).description).toBe('Saved edit');
  rmSync(target, { recursive: true });
  database.write(tx => tx.run('DROP TABLE managed_skill_versions'));
  try { save_skill(database, 'example', { description: 'Published edit' }, { now: 2, database_parent: path }); } catch (e) { error = e; }
  expect((error as PlannerError).detail).toEqual({ skill_name: 'example', saved: true, published: true, phase: 'history' });
  expect(readFileSync(target, 'utf8')).toBe(database.read(sql => read_skill(sql, 'example')).source_text);
  rmSync(target); mkdirSync(target);
  const preset = definition_from_json(JSON.stringify(shipped_worker_types()[0]));
  const definition = { ...preset, worker_type: 'example', worker_profile: { ...preset.worker_profile, specialist_skill: 'example' } };
  try { save_worker_type_definition(database, definition, null, { now: 3, database_parent: path }); } catch (e) { error = e; }
  expect((error as PlannerError).detail).toEqual({ skill_name: 'example', saved: true, published: false, phase: 'file', worker_type: 'example', definition_saved: true });
  expect(configured_worker_type_registry().require('example')).toEqual(definition);
  expect(database.read(sql => sql.get('SELECT worker_type FROM worker_types WHERE worker_type = ?', ['example']))).toEqual({ worker_type: 'example' });
});
