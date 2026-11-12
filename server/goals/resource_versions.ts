import type { FastifyRequest } from 'fastify';
import type { Clock } from '../core/clock.ts';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../resource_versions/contracts.ts';
import { calendar_now, calendar_parts } from './calendar.ts';
import { read_goal } from './data.ts';
import { goal_json, list_json, reading_calendar, readings_json } from './views.ts';
let clock: Clock;
export function configure_goal_resources(value: Clock): void {
  clock = value;
}
function revision(sql: ReadSql, kind: string): string {
  return String(
    sql.get<{ revision: number }>(
      'SELECT revision FROM resource_source_versions WHERE source_kind=? AND source_id=?',
      [kind, 'all'],
    )?.revision ?? 0,
  );
}
export class GoalResources implements ResourceProvider {
  capture(sql: ReadSql, _request: FastifyRequest, key: string): ResourceSnapshot | null {
    const url = new URL(key, 'http://comparison.invalid'),
      parts = url.pathname.replace(/^\/api\//, '').split('/'),
      q = url.searchParams;
    if (parts[0] !== 'goals') return null;
    if ([...q.keys()].some((k) => q.getAll(k).length !== 1)) return null;
    const now = calendar_now(clock);
    let rev = revision(sql, 'goals') + ':' + calendar_parts(now)[0];
    if (parts.length === 1 && [...q.keys()].every((k) => ['active', 'project_id'].includes(k))) {
      const active = q.get('active');
      if (active !== null && active !== 'true' && active !== 'false') return null;
      return {
        revision: rev,
        read: () =>
          list_json(sql, now, {
            active: active === null ? null : active === 'true',
            project_id: q.get('project_id'),
          }),
      };
    }
    if (parts.length === 2 && q.size === 0)
      return { revision: rev, read: () => goal_json(sql, read_goal(sql, parts[1]!), now) };
    if (
      parts.length === 3 &&
      parts[2] === 'readings' &&
      [...q.keys()].every((k) => ['first_date', 'last_date'].includes(k))
    ) {
      rev +=
        ':' +
        revision(sql, 'automatic-measures') +
        ':' +
        reading_calendar(sql, read_goal(sql, parts[1]!), now)[0];
      return {
        revision: rev,
        read: () =>
          readings_json(sql, parts[1]!, now, {
            first_date: q.get('first_date'),
            last_date: q.get('last_date'),
          }),
      };
    }
    return null;
  }
}
