import type { Principal } from '../core/contracts.ts';
import type { JsonNumber } from '../core/http/numeric_json.ts';
export type CountPeriod = 'day' | 'week' | 'month';
export type MeasureKind = 'reading' | 'running_total' | 'period_count';
export type MeasureOrigin = 'taken' | 'assembled' | 'manual';
export type Coverage = 'complete' | 'partial' | 'unknown' | 'not_applicable';
export type Status =
  | 'value'
  | 'missing_input'
  | 'division_by_zero'
  | 'invalid_period'
  | 'arithmetic_limit'
  | 'invalid_value';
export type FormulaExpression =
  | { op: 'constant'; value: number | JsonNumber }
  | { op: 'reference'; measure_id: string; day_offset: number | JsonNumber }
  | { op: 'reference_at'; measure_id: string; date: string }
  | { op: 'elapsed_days'; start: string }
  | { op: 'sum_daily'; measure_id: string; period: CountPeriod }
  | { op: 'if_before'; date: string; before: FormulaExpression; otherwise: FormulaExpression }
  | {
      op: 'add' | 'subtract' | 'multiply' | 'divide' | 'power' | 'min' | 'max';
      left: FormulaExpression;
      right: FormulaExpression;
    };
export interface Measure {
  id: string;
  project_id: string;
  name: string;
  unit: string;
  origin: MeasureOrigin;
  kind: MeasureKind;
  timezone: string;
  period: CountPeriod | null;
  source_id: string | null;
  formula: FormulaExpression | null;
  definition_revision: number;
  created_at: number;
  updated_at: number;
  write_revision: number;
}
export interface MeasureDate {
  measure_id: string;
  date: string;
}
export interface PeriodInterval {
  start_date: string;
  end_date_exclusive: string;
}
export interface OutputMeasure {
  measure_id: string;
  definition_revision: number;
  kind: MeasureKind;
  timezone: string;
  period: CountPeriod | null;
  required_date: string;
  period_interval: PeriodInterval | null;
}
export interface Observation extends MeasureDate {
  value: number;
  run_id: string | null;
  observed_at: number;
  definition_revision: number;
  period_interval: PeriodInterval | null;
  manual_write_id: string | null;
  query_start_at: number | null;
  query_end_at: number | null;
  limitation: string | null;
  coverage_status: Coverage;
  coverage_reasons: string[];
}
export interface InputObservation extends MeasureDate {
  definition_revision: number;
  value: number | null;
  run_id: string | null;
  manual_write_id: string | null;
  query_start_at: number | null;
  query_end_at: number | null;
  limitation: string | null;
  coverage_status: Coverage;
  coverage_reasons: string[];
}
export interface ExpressionValue {
  status: Status;
  value: number | null;
  inputs: InputObservation[];
  missing_inputs: MeasureDate[];
  coverage_status: Coverage;
  coverage_reasons: string[];
}
export interface MeasureValue extends ExpressionValue, MeasureDate {
  definition_revision: number;
  query_start_at: number | null;
  query_end_at: number | null;
  limitation: string | null;
}
export interface ManualWriteBody {
  request_id: string;
  date: string;
  expected_definition_revision: number | JsonNumber;
  basis: 'date' | 'latest';
  operation: 'replace' | 'increment';
  value?: number | JsonNumber;
  delta?: number | JsonNumber;
  expected_write_revision?: number | JsonNumber;
}
export interface ManualMeasureState {
  measure_id: string;
  definition_revision: number;
  write_revision: number;
  timezone: string;
  current_date: string;
  date: string;
  observation: Observation | null;
  latest_observation: Observation | null;
}
export interface ManualWriteReceipt {
  request_id: string;
  measure_id: string;
  actor: Principal;
  payload_fingerprint: string;
  date: string;
  value: number;
  write_revision: number;
  accepted_at: number;
}
export type Definitions = Map<string, Measure>;
export type Observations = Map<string, Observation>;
export const pair_key = (pair: MeasureDate): string => `${pair.measure_id}\0${pair.date}`;
