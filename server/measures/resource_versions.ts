import type { FastifyRequest } from 'fastify';
import type { Clock } from '../core/clock.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../resource_versions/contracts.ts';
import { list_measures, read_measure } from './data.ts';
import { read_state } from './manual.ts';
import { local_date } from './logic/dates.ts';
let clock: Clock;
export function configure_measure_resources(value: Clock): void {
  clock = value;
}
export class MeasureResources implements ResourceProvider {
  capture(sql: ReadSql, _request: FastifyRequest, key: string): ResourceSnapshot | null {
    const url = new URL(key, 'http://comparison.invalid');
    if (!url.pathname.startsWith('/api/measures')) return null;
    const q = url.searchParams;
    if ([...q.keys()].some((k) => q.getAll(k).length !== 1)) return null;
    const parts = url.pathname.slice('/api/'.length).split('/');
    const revision = String(
      sql.get<{ revision: number }>(
        'SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?',
        ['automatic-measures', 'all'],
      )?.revision ?? 0,
    );
    if (parts.length === 3 && parts[0] === 'measures' && parts[2] === 'manual-state') {
      if ([...q.keys()].some((k) => k !== 'date')) return null;
      const now = clock.nowUnix(),
        m = read_measure(sql, parts[1]!);
      return {
        revision: revision + ':' + local_date(now, m.timezone),
        read: () => read_state(sql, m.id, q.get('date'), now),
      };
    }
    if (
      [...q.keys()].some(
        (k) => !['project_id', 'source_id', 'account', 'limit', 'offset'].includes(k),
      )
    )
      return null;
    const parse = (raw: string | null, defaultValue: number) =>
      raw === null ? defaultValue : /^[+-]?\d+$/.test(raw.trim()) ? Number(raw) : NaN;
    const limit = parse(q.get('limit'), 25),
      offset = parse(q.get('offset'), 0);
    if (!Number.isFinite(limit) || !Number.isFinite(offset)) return null;
    if (url.pathname === '/api/measures')
      return {
        revision,
        read: () => list_measures(sql, q.get('project_id'), q.get('source_id'), limit, offset),
      };
    if (parts.length === 2 && parts[0] === 'measures')
      return { revision, read: () => read_measure(sql, parts[1]!) };
    return null;
  }
}
