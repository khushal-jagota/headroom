import { PlannerError } from '../../core/contracts.ts';

// Python str.strip includes the four ASCII information separators, unlike JS trim.
export function strip(value: string): string {
  return value.replace(/^[\p{White_Space}\u001c-\u001f]+|[\p{White_Space}\u001c-\u001f]+$/gu, '');
}
export function projectIdForName(name: string): string {
  const slug = strip(name)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
  if (!slug) throw new PlannerError('validation', 'project name is required');
  return `project_${slug}`;
}
export function bodyString(body: Record<string, unknown>, key: string, fallback = ''): string {
  const raw = key in body ? body[key] : fallback;
  if (typeof raw !== 'string')
    throw new PlannerError('validation', `invalid ${key}`, { [key]: raw });
  return raw;
}
export function optionalBodyString(body: Record<string, unknown>, key: string): string | null {
  return body[key] == null ? null : bodyString(body, key);
}
export function nullableFolderPath(raw: unknown): string | null {
  if (raw !== null && typeof raw !== 'string')
    throw new PlannerError('validation', 'invalid folder_path', { folder_path: raw });
  return raw;
}
