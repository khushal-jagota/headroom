import type { NumericJSON } from '../core/http/numeric_json.ts';
import { JsonNumber } from '../core/http/numeric_json.ts';
import type { SourceHealthSummary } from '../../web/src/lib/goals/contracts.ts';
import type { Dataset, DatasetResult } from '../datasets/contracts.ts';
export type { ChartBlock as ChartInput, PageDocument as PageInput } from './contracts.ts';
export interface DatasetReading {
  first_date: string;
  last_date: string;
  datasets: Dataset[];
  results: DatasetResult[];
  labels: { dataset_id: string; dimension: string; code: string; label: string }[];
  missing_dates: { dataset_id: string; date: string }[];
  sources: (Omit<SourceHealthSummary, 'health'> & { health: string })[];
  returned_rows: number;
  matching_rows: number;
  truncated: boolean;
}
/** Project retained numeric markers for arithmetic without changing the authored document. */
export function numeric_projection<T>(value: NumericJSON<T>): T {
  if (value instanceof JsonNumber) return value.value as T;
  if (Array.isArray(value)) return value.map((item) => numeric_projection(item)) as T;
  if (value !== null && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, numeric_projection(item)]),
    ) as T;
  return value as T;
}
export const text_order = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
