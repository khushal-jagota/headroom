/** Client-only collection setup. The collector retains database access in collection.ts. */
import { isAbsolute } from 'node:path';
import { TIMEZONE } from './configuration.ts';
export const ATTENTION_SOURCE_ACCOUNT = 'panels-attention-history';
export const COLLECTION_LOCAL_TIME = '00:05';
export const DAILY_METRICS = [
  'owner_messages',
  'owner_approvals',
  'approval_wait_seconds',
] as const;
export const COLLECTOR_PAGE_LIMIT = 50;
export type Ids = Record<string, string>;
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
