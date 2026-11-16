/** One Measure record order for route, resource-body, and domain-produced records. */
import type { Measure } from './contracts.ts';
import { ordered_record } from '../core/http/ordered_record.ts';
const fields = [
  'id',
  'project_id',
  'name',
  'unit',
  'origin',
  'kind',
  'timezone',
  'period',
  'source_id',
  'formula',
  'definition_revision',
  'created_at',
  'updated_at',
  'write_revision',
] as const;
export function measure_json(measure: Measure): Measure {
  return ordered_record(measure, fields, 'Measure');
}
