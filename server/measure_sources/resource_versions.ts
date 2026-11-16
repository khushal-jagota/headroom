import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceSnapshot } from '../resource_versions/contracts.ts';
import { list_metadata } from '../credentials/data.ts';
import * as data from './data.ts';
import * as views from './views.ts';

/** Complete the automatic-measures provider with source and credential reads. */
export function capture_source_resource(
  sql: ReadSql,
  url: URL,
  now: number,
  revision: string,
): ResourceSnapshot | null {
  const query = url.searchParams;
  if ([...query.keys()].some((key) => query.getAll(key).length !== 1)) return null;
  if (
    [...query.keys()].some(
      (key) => !['project_id', 'source_id', 'account', 'limit', 'offset'].includes(key),
    )
  )
    return null;
  const parse = (raw: string | null, fallback: number) =>
    raw === null ? fallback : /^[+-]?\d+$/.test(raw.trim()) ? Number(raw) : NaN;
  const limit = parse(query.get('limit'), 30);
  const offset = parse(query.get('offset'), 0);
  if (!Number.isFinite(limit) || !Number.isFinite(offset)) return null;
  views.validate_page(limit, offset);
  const parts = url.pathname.slice('/api/'.length).split('/');
  if (url.pathname === '/api/measure-sources')
    return {
      revision,
      read: () => views.list_json(sql, { project_id: query.get('project_id'), limit, offset, now }),
    };
  if (parts[0] === 'measure-sources' && (parts.length === 2 || parts.length === 3)) {
    const id = parts[1]!;
    if (parts.length === 2) return { revision, read: () => data.read_source(sql, id) };
    if (parts[2] === 'status') return { revision, read: () => views.status(sql, id, { now }) };
    if (parts[2] === 'runs')
      return { revision, read: () => views.runs_json(sql, id, { limit, offset }) };
  }
  if (parts[0] === 'measure-source-runs' && parts.length === 2)
    return { revision, read: () => data.read_run_detail(sql, parts[1]!) };
  if (url.pathname === '/api/credentials')
    return {
      revision,
      read: () => list_metadata(sql, query.has('limit') ? limit : 25, offset),
    };
  return null;
}
