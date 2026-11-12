import { isMap, parseDocument } from 'yaml';
import { PlannerError } from '../core/contracts.ts';
import type { ManagedSkill, SkillPatch } from './contracts.ts';
function fail(message: string, detail: Record<string, unknown> = {}): never { throw new PlannerError('validation', message, detail); }
export function frontmatter_bounds(text: string): [string[], string] {
  const lines = text.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g)?.filter(Boolean) ?? [];
  if (!lines.length || lines[0].trim() !== '---') fail('skill frontmatter is missing');
  for (let i = 1; i < lines.length; i++) if (lines[i].trim() === '---') return [lines.slice(1, i), lines.slice(i + 1).join('')];
  return fail('skill frontmatter is not closed');
}
export function skill_description(text: string, expected_skill_name: string): string {
  const [lines] = frontmatter_bounds(text);
  const doc = parseDocument(lines.join(''), { uniqueKeys: false, version: '1.1' });
  if (doc.errors.length) fail('skill frontmatter is invalid', { skill_name: expected_skill_name });
  const value: unknown = doc.toJS();
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail('skill frontmatter must be a mapping', { skill_name: expected_skill_name });
  const data = value as Record<string, unknown>;
  if (data.name !== expected_skill_name) fail('specialist skill name is immutable', { skill_name: data.name ?? null, expected: expected_skill_name });
  if (typeof data.description !== 'string' || !data.description) fail('specialist skill description is required', { skill_name: expected_skill_name });
  return data.description;
}
export function parse_skill(source_text: string, name: string): ManagedSkill {
  return { name, description: skill_description(source_text, name), markdown_body: frontmatter_bounds(source_text)[1], source_text };
}
// Python json.dumps uses ASCII escapes, including surrogate pairs for non-BMP text.
export function python_json_string(value: string): string { return JSON.stringify(value).replace(/[\u007f-\uffff]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`); }
export function render_skill_from_existing_frontmatter(existing: string, values: { expected_skill_name: string; description: string; markdown_body: string }): string {
  const [lines] = frontmatter_bounds(existing), source = lines.join('');
  const doc = parseDocument(source, { uniqueKeys: false, version: '1.1' });
  if (doc.errors.length) fail('skill frontmatter is invalid');
  if (!isMap(doc.contents)) fail('skill frontmatter must be a mapping');
  const spans = new Map<string, [number, number]>();
  const lineOf = (offset: number): number => source.slice(0, offset).split('\n').length - 1;
  for (const pair of doc.contents.items) {
    const key = String(pair.key);
    if (key !== 'name' && key !== 'description') continue;
    if (spans.has(key)) fail('skill frontmatter has duplicate keys', { key });
    const keyNode = pair.key as { range?: [number, number, number] }, valueNode = pair.value as { range?: [number, number, number] } | null;
    const start = lineOf(keyNode.range?.[0] ?? 0), endOffset = valueNode?.range?.[1] ?? keyNode.range?.[1] ?? 0;
    const end = lineOf(endOffset) + (endOffset > 0 && source[endOffset - 1] !== '\n' ? 1 : 0);
    spans.set(key, [start, Math.min(lines.length, end)]);
  }
  const replacement: Record<string, string> = { name: `name: ${python_json_string(values.expected_skill_name)}\n`, description: `description: ${python_json_string(values.description)}\n` };
  const result = ['---\n'];
  for (let i = 0; i < lines.length;) {
    const key = [...spans].find(([, [start]]) => start === i)?.[0];
    if (!key) result.push(lines[i++]);
    else { result.push(replacement[key]); i = spans.get(key)![1]; }
  }
  for (const key of ['name', 'description']) if (!spans.has(key)) result.push(replacement[key]);
  result.push('---\n');
  if (values.markdown_body && !values.markdown_body.startsWith('\n')) result.push('\n');
  result.push(values.markdown_body);
  if (values.markdown_body && !values.markdown_body.endsWith('\n')) result.push('\n');
  return result.join('');
}
export function render_new_skill(name: string, description: string, markdown_body: string): string {
  return render_skill_from_existing_frontmatter(`---\nname: ${python_json_string(name)}\n---\n`, { expected_skill_name: name, description, markdown_body });
}
export function require_skill_name(name: string): void {
  if (!name || name.startsWith('.') || /[/\\\0]/.test(name)) fail('skill name must be a safe single directory component', { field: 'name' });
}
export function normalize_skill_patch(name: string, raw: Record<string, unknown>): SkillPatch {
  require_skill_name(name);
  const unexpected = Object.keys(raw).filter(k => !['name', 'description', 'markdown_body', 'body'].includes(k)).sort();
  if (unexpected.length) fail('unknown skill field', { field: unexpected[0] });
  if ('name' in raw && raw.name !== name) fail('skill name is immutable', { field: 'name', expected: name });
  if ('body' in raw && 'markdown_body' in raw) fail('provide only one of body and markdown_body', { field: 'markdown_body' });
  const patch: SkillPatch = {};
  for (const field of ['description', 'markdown_body'] as const) {
    const source = field === 'markdown_body' && 'body' in raw ? 'body' : field;
    if (!(source in raw)) continue;
    const value = raw[source];
    if (typeof value !== 'string' || !value.trim()) fail(`${field} must be nonblank text`, { field });
    patch[field] = value;
  }
  if (!Object.keys(patch).length) fail('skill patch requires description or markdown_body', { field: 'description' });
  return patch;
}
