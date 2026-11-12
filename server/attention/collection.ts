/** Retain daily attention through the ordinary trusted source contract. */
import { isAbsolute } from 'node:path';
import { createReadAccess } from '../core/database/index.ts';
import { calendar_date, local_date } from '../measures/logic/dates.ts';
import { stringify_python_json } from '../core/http/numeric_json.ts';
import { read_summary } from './actions.ts';
import { summary_request } from './contracts.ts';
import { TIMEZONE } from './configuration.ts';
import type { HistoryWindow } from '../measure_sources/contracts.ts';
export const ATTENTION_SOURCE_ACCOUNT = 'panels-attention-history';
export const COLLECTION_LOCAL_TIME = '00:05';
export const DAILY_METRICS = [
  'owner_messages',
  'owner_approvals',
  'approval_wait_seconds',
] as const;
export const COLLECTOR_PAGE_LIMIT = 50;
type Ids = Record<string, string>;
export interface CollectorContext {
  frozen_at: number;
  history: HistoryWindow | null;
}
export interface CollectorHelpers {
  context: CollectorContext;
  record: (
    id: string,
    date: string,
    value: number | null,
    options: Record<string, unknown>,
  ) => unknown | Promise<unknown>;
  record_dataset: (
    id: string,
    date: string,
    rows: unknown[],
    options: Record<string, unknown>,
  ) => unknown | Promise<unknown>;
}
export function collection_script(
  database_path: string,
  dataset_ids: Ids,
  measure_ids: Ids,
): string {
  return `import {collect_daily} from 'panels:attention';\nexport default async function ({context, record, record_dataset}) {\n  await collect_daily(${JSON.stringify(database_path)}, ${JSON.stringify(dataset_ids)}, ${JSON.stringify(measure_ids)}, {context, record, record_dataset});\n}\n`;
}
export type CollectionSend = (
  method: string,
  path: string,
  options?: { params?: Record<string, string | number>; json_body?: unknown },
) => Promise<any>;
export async function setup_collection(
  send: CollectionSend,
  {
    project_id,
    database_path,
    local_time = COLLECTION_LOCAL_TIME,
  }: { project_id: string; database_path: string; local_time?: string },
) {
  if (typeof database_path !== 'string' || !isAbsolute(database_path))
    throw new Error('attention collection needs an absolute server database path');
  const listing = async (path: string, key: string, filters: Record<string, string> = {}) => {
    const found: any[] = [];
    let offset = 0;
    for (;;) {
      const page = await send('GET', path, {
        params: { ...filters, limit: COLLECTOR_PAGE_LIMIT, offset },
      });
      found.push(...page[key]);
      const next = page.page.next_offset;
      if (next === null) return found;
      offset = next;
    }
  };
  const matches = (await listing('/api/measure-sources', 'sources', { project_id }))
    .map((entry) => entry.source)
    .filter(
      (s) =>
        s.scope.account === ATTENTION_SOURCE_ACCOUNT &&
        s.responsible.kind === 'chief' &&
        s.responsible.id === 'chief',
    );
  if (matches.length > 1) throw new Error('multiple attention sources need explicit repair');
  let source = matches.length
    ? await send('GET', '/api/measure-sources/' + matches[0].id)
    : await send('POST', '/api/measure-sources', {
        json_body: {
          name: 'Historical owner attention',
          project_id,
          account: ATTENTION_SOURCE_ACCOUNT,
          responsible: { kind: 'chief', id: 'chief' },
        },
      });
  const path = '/api/measure-sources/' + source.id,
    existing_datasets = await listing('/api/datasets', 'datasets', { source_id: source.id }),
    existing_measures = await listing('/api/measures', 'measures', { source_id: source.id }),
    dataset_ids: Ids = {},
    measure_ids: Ids = {};
  for (const metric of DAILY_METRICS) {
    const name = 'Historical attention: ' + metric,
      old_datasets = existing_datasets.filter((item) => item.name === name),
      old_measures = existing_measures.filter((item) => item.name === name);
    if (old_datasets.length > 1 || old_measures.length > 1)
      throw new Error('duplicate attention outputs need explicit repair');
    const seconds = metric.endsWith('seconds'),
      unit = seconds ? 'seconds' : 'events';
    if (old_datasets.length) dataset_ids[metric] = old_datasets[0].id;
    else {
      const item = await send('POST', '/api/datasets', {
        json_body: {
          project_id,
          source_id: source.id,
          name,
          timezone: TIMEZONE,
          dimensions: [
            ['destination_kind', 'Destination kind'],
            ['destination_id', 'Destination'],
            ['project_id', 'Project'],
            ['manager_id', 'Manager'],
            ['attribution_basis', 'Attribution basis'],
            ['coverage_status', 'Coverage'],
          ].map(([name, label]) => ({ name, label })),
          fields: [{ name: 'value', label: metric, unit, kind: seconds ? 'amount' : 'count' }],
        },
      });
      dataset_ids[metric] = item.id;
    }
    if (old_measures.length) measure_ids[metric] = old_measures[0].id;
    else {
      const item = await send('POST', '/api/measures', {
        json_body: {
          project_id,
          source_id: source.id,
          name,
          timezone: TIMEZONE,
          unit,
          origin: 'taken',
          kind: 'period_count',
          period: 'day',
          formula: null,
        },
      });
      measure_ids[metric] = item.id;
    }
  }
  source = await send('GET', path);
  const script = collection_script(database_path, dataset_ids, measure_ids);
  if (source.active_script === null || source.active_script.text !== script)
    source = await send('PUT', path + '/script', {
      json_body: { expected_revision: source.source_revision, text: script },
    });
  if (!source.enabled || source.local_time !== local_time)
    await send('PUT', path + '/schedule', {
      json_body: { expected_revision: source.source_revision, enabled: true, local_time },
    });
  return { source_id: source.id, dataset_ids, measure_ids };
}
function next_day(day: string): string {
  const d = calendar_date(day);
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0, 10);
}
function previous_day(day: string): string {
  const d = calendar_date(day);
  d.setUTCDate(d.getUTCDate() - 1);
  return d.toISOString().slice(0, 10);
}
export async function collect_daily(
  database_path: string,
  dataset_ids: Ids,
  measure_ids: Ids,
  { context, record, record_dataset }: CollectorHelpers,
): Promise<void> {
  if (typeof database_path !== 'string' || !isAbsolute(database_path))
    throw new TypeError('attention collection needs an absolute server database path');
  const now = Math.trunc(context.frozen_at),
    today = local_date(now, TIMEZONE),
    first = context.history?.first_date ?? previous_day(today),
    last = context.history?.last_date ?? today;
  calendar_date(first);
  calendar_date(last);
  if (first > last || last > today) throw new Error('attention history dates are invalid');
  const access = createReadAccess(database_path);
  let days: { day: string; report: ReturnType<typeof read_summary> }[];
  try {
    days = access.read((sql) => {
      const out: { day: string; report: ReturnType<typeof read_summary> }[] = [];
      for (let day = first; day <= last; day = next_day(day)) {
        const request = summary_request({
            first_date: day,
            last_date: day,
            group_by: 'ticket',
            limit: COLLECTOR_PAGE_LIMIT,
          }),
          report = read_summary(sql, request, now),
          all_rows = [...report.rows];
        while (report.page.next_offset !== null) {
          const page = read_summary(sql, { ...request, offset: report.page.next_offset }, now);
          all_rows.push(...page.rows);
          report.page = page.page;
        }
        report.rows = all_rows;
        out.push({ day, report });
      }
      return out;
    });
  } finally {
    access.close();
  }
  for (const { day, report } of days)
    for (const metric of DAILY_METRICS) {
      const total = report.totals.current[metric],
        limitations = [...total.limitations],
        rows: unknown[] = [],
        labels: Record<string, Record<string, string>> = { destination_id: {} };
      let missing = 0;
      for (const group of report.rows) {
        const observation = group.current[metric];
        if (observation.value === null) {
          missing++;
          continue;
        }
        rows.push({
          dimensions: {
            destination_kind: group.kind,
            destination_id: group.id,
            project_id: group.project_id ?? '',
            manager_id: group.manager_id ?? '',
            attribution_basis: stringify_python_json(group.attribution_basis, {
              compact: true,
              ensure_ascii: true,
            }),
            coverage_status: observation.coverage_status,
          },
          values: { value: observation.value },
        });
        labels.destination_id![group.id] = group.title;
      }
      if (missing)
        limitations.push(`${missing} destination values are unknown and absent from numeric rows.`);
      const limitation = [...new Set(limitations)].join(' ') || null,
        coverage = {
          query_start_at: report.period.start_at,
          query_end_at: report.period.end_at,
          limitation,
        };
      await record_dataset(dataset_ids[metric]!, day, rows, {
        ...coverage,
        labels,
        unavailable_reason: total.value === null && !rows.length ? 'provider_has_no_history' : null,
      });
      await record(measure_ids[metric]!, day, total.value, {
        ...coverage,
        unavailable_reason: total.value === null ? 'provider_has_no_history' : null,
      });
    }
}
