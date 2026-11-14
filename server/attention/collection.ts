/** Retain daily attention through the ordinary trusted source contract. */
import { isAbsolute } from 'node:path';
import { createReadAccess } from '../core/database/index.ts';
import { calendar_date, local_date } from '../measures/logic/dates.ts';
import { read_summary } from './actions.ts';
import { summary_request } from './contracts.ts';
import { TIMEZONE } from './configuration.ts';
import type { HistoryWindow } from '../measure_sources/contracts.ts';
import { DAILY_METRICS, COLLECTOR_PAGE_LIMIT } from './setup.ts';
import type { Ids } from './setup.ts';
export {
  ATTENTION_SOURCE_ACCOUNT,
  COLLECTION_LOCAL_TIME,
  DAILY_METRICS,
  COLLECTOR_PAGE_LIMIT,
  collection_script,
  setup_collection,
} from './setup.ts';
export type { CollectionSend } from './setup.ts';
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
            attribution_basis: JSON.stringify(group.attribution_basis),
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
