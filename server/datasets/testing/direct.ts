import { dimension_key } from '../logic/validation.ts';
import { createReadAccess } from '../../core/database/index.ts';
import { read_values, earliest_retained_date } from '../data/index.ts';
import type { ReadDatasetsBody } from '../contracts.ts';
export function run(spec: { db_path: string; now: number; requests: ReadDatasetsBody[] }) {
  const access = createReadAccess(spec.db_path);
  try {
    return access.read((sql) => ({
      unicode_tuple_key: dimension_key({ '\u{10000}': '\u007f', '\ue000': 'é' }),
      reads: spec.requests.map((request) => read_values(sql, request, spec.now)),
      earliest: earliest_retained_date(sql, spec.requests[0]!.dataset_ids[0]!),
    }));
  } finally {
    access.close();
  }
}
