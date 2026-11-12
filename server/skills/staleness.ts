import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import type { ReadSql } from '../core/database/contracts.ts';
import type { CommandNode, StaleReference } from './contracts.ts';
import { PACKAGED_SKILLS_ROOT, read_all_skill_sources } from './data.ts';
import { RETIRED_PANELS_SKILL_NAMES } from './sources.ts';
const CREATE_KEYS = new Set(['worker_type', 'workflow', 'employee_backend', 'employee_launch_model', 'employee_launch_reasoning_effort', 'locale', 'title', 'brief', 'deadline', 'project', 'project_id', 'manager', 'blocked_by_ticket_ids', 'ceiling', 'ceiling_holder']);
function command_resolves(words: string[], root: CommandNode): boolean {
  let node: CommandNode | null = root;
  for (const word of words) { if (node === null) return true; if (!(word in node)) return false; node = node[word]; }
  return true;
}
export function ticket_create_examples(text: string): Record<string, unknown>[] {
  const lines = text.split(/\r?\n/), result: Record<string, unknown>[] = [];
  for (let i = 0; i < lines.length; i++) {
    const match = /^ {0,3}(`{3,}|~{3,})(.*)$/.exec(lines[i]);
    if (!match || (match[1][0] === '`' && match[2].includes('`'))) continue;
    const body: string[] = [], closing = new RegExp(`^ {0,3}${match[1][0]}{${match[1].length},}[ \\t]*$`);
    let complete = false;
    while (++i < lines.length) { if (closing.test(lines[i])) { complete = true; break; } body.push(lines[i]); }
    if (!complete || !['sh','bash','shell'].includes(match[2].trim())) continue;
    while (body.length && !body[0].trim()) body.shift();
    while (body.length && !body.at(-1)!.trim()) body.pop();
    if (body.length < 3) continue;
    const command = /^panels[ \t]+ticket[ \t]+create[ \t]+--input-json[ \t]+-[ \t]+<<(['"])([A-Za-z_][A-Za-z_0-9]*)\1[ \t]*$/.exec(body[0]);
    if (!command || body.at(-1) !== command[2] || body.slice(1,-1).includes(command[2])) continue;
    try { const data: unknown = JSON.parse(body.slice(1,-1).join('\n')); if (data && typeof data === 'object' && !Array.isArray(data)) result.push(data as Record<string, unknown>); } catch { /* Nonliteral examples do not declare a contract. */ }
  }
  return result;
}
function validPrincipal(value: unknown, manager: boolean): boolean {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const p = value as Record<string, unknown>;
  if (Object.keys(p).sort().join(',') !== 'id,kind' || typeof p.id !== 'string' || typeof p.kind !== 'string') return false;
  if (!['owner','chief','ticket','manager','goal'].includes(p.kind) || !p.id.trim() || p.id !== p.id.trim()) return false;
  if ((p.kind === 'owner' && p.id !== 'owner') || (p.kind === 'chief' && p.id !== 'chief')) return false;
  return !manager || p.kind === 'manager' || p.kind === 'chief';
}
export function stale_references(sql: ReadSql): StaleReference[] {
  const commands = JSON.parse(readFileSync(new URL('./cli_commands.json', import.meta.url), 'utf8')) as CommandNode;
  const ids = new Set<string>(), stageNames = new Set<string>(), terminal = new Set<string>();
  for (const row of sql.all<{ definition_json: string }>('SELECT definition_json FROM worker_types ORDER BY position')) {
    const definition = JSON.parse(row.definition_json) as { stages: { id: string; label?: string }[] };
    for (const stage of definition.stages) { ids.add(stage.id); stageNames.add(stage.id); if (stage.label) stageNames.add(stage.label); }
    const last = definition.stages.at(-1)!; terminal.add(last.id); if (last.label) terminal.add(last.label);
  }
  const found = new Map<string, StaleReference>();
  const add = (skill_name: string, kind: string, reference: string) => { const value = { skill_name, kind, reference }; found.set(JSON.stringify(value), value); };
  const examples = (name: string, source: string, prefix = '') => {
    for (const payload of ticket_create_examples(source)) {
      for (const key of Object.keys(payload)) if (!CREATE_KEYS.has(key)) add(name, `${prefix}ticket_create_field`, key);
      for (const field of ['manager','ceiling_holder']) if (field in payload && !validPrincipal(payload[field], field === 'manager')) add(name, `${prefix}ticket_create_assignment`, field);
    }
  };
  for (const [name, source] of Object.entries(read_all_skill_sources(sql))) {
    examples(name, source);
    for (const match of source.matchAll(/`panels ([^`]{0,200}?)`/gs)) {
      const words: string[] = [];
      for (const word of match[1].split(/\s+/).filter(Boolean)) { if (/^[-<[.(…]/.test(word)) break; words.push(word); }
      if (words.length && !command_resolves(words, commands)) add(name, 'command', `panels ${words.join(' ')}`);
    }
    if (name !== 'probe-worker') {
      for (const match of source.matchAll(/needs(?:\\_|_)([a-z](?:[a-z]|\\_|_)*)/g)) { const id = 'needs_' + match[1].replaceAll('\\','').replace(/_+$/, ''); if (!ids.has(id)) add(name, 'stage', id); }
      for (const match of source.matchAll(/\*\*([^*]*?→[^*]*?)\*\*/gs)) {
        const names = match[1].split('→').map(s => s.trim().replace(/\s+/g,' ').replace(/^\.+|\.+$/g,''));
        if (names[0].includes(':')) names[0] = names[0].slice(names[0].lastIndexOf(':') + 1).trim();
        if (names.length >= 2 && terminal.has(names.at(-1)!)) for (const part of names) if (!stageNames.has(part)) add(name, 'ladder', part);
      }
    }
    for (const retired of RETIRED_PANELS_SKILL_NAMES) if (source.includes(retired)) add(name, 'retired_skill', retired);
  }
  for (const entry of readdirSync(PACKAGED_SKILLS_ROOT, { withFileTypes: true })) if (entry.isDirectory()) {
    try { examples(entry.name, readFileSync(join(PACKAGED_SKILLS_ROOT, entry.name, 'SKILL.md'), 'utf8'), 'packaged_'); } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
  }
  return [...found.values()].sort((a,b) => { const ak = [a.skill_name,a.kind,a.reference].join('\0'), bk = [b.skill_name,b.kind,b.reference].join('\0'); return ak < bk ? -1 : ak > bk ? 1 : 0; });
}
