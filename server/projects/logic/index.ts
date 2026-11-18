import { PlannerError } from '../../core/contracts.ts';

export function projectIdForName(name: string): string {
  const slug = name
    .trim()
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
export function nullableString(raw: unknown, field: string): string | null {
  if (raw !== null && typeof raw !== 'string')
    throw new PlannerError('validation', `invalid ${field}`, { [field]: raw });
  return raw;
}
export function normalizeRepositoryUrl(raw: string | null): string | null {
  if (raw === null) return null;
  const value = raw.trim();
  if (!value) throw new PlannerError('validation', 'repository_url cannot be empty');
  if (value.startsWith('-') || /[\s\x00-\x1f\x7f]/.test(value))
    throw new PlannerError('validation', 'invalid repository_url', { repository_url: raw });
  return value;
}
export function normalizeDefaultBranch(raw: string | null): string | null {
  if (raw === null) return null;
  const value = raw.trim();
  if (!value) throw new PlannerError('validation', 'default_branch cannot be empty');
  if (
    value.startsWith('-') ||
    value.startsWith('/') ||
    value.endsWith('/') ||
    value.endsWith('.lock') ||
    value.includes('..') ||
    value.includes('@{') ||
    value.includes('\\') ||
    /[\s\x00-\x1f\x7f~^:?*[]/.test(value)
  )
    throw new PlannerError('validation', 'invalid default_branch', { default_branch: raw });
  return value;
}
export function resolveProjectRepository(
  current: { repository_url: string | null; default_branch: string | null },
  patch: { repository_url?: string | null; default_branch?: string | null },
): { repository_url: string | null; default_branch: string | null } {
  const repository_url =
    patch.repository_url !== undefined
      ? normalizeRepositoryUrl(patch.repository_url)
      : current.repository_url;
  const default_branch =
    patch.default_branch !== undefined
      ? normalizeDefaultBranch(patch.default_branch)
      : patch.repository_url === null
        ? null
        : current.default_branch;
  if (default_branch !== null && repository_url === null)
    throw new PlannerError('validation', 'default_branch requires repository_url');
  return { repository_url, default_branch };
}
