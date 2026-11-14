import type * as Wire from '../../web/src/lib/goals/contracts.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
export type CountPeriod = Wire.SumDailyExpression['period'];
export type MeasureKind = 'reading' | 'running_total' | 'period_count';
export type MeasureOrigin = 'taken' | 'assembled' | 'manual';
export type Coverage = Wire.ExpressionValue['coverage_status'];
export type Status = Wire.ExpressionValue['status'];
/** The stored form retains numeric kinds and exact integer digits until projection. */
export type FormulaExpression = NumericJSON<Wire.FormulaExpression>;
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
export type MeasureDate = Wire.MeasureDate;
export type PeriodInterval = Wire.PeriodInterval;
export interface OutputMeasure {
  measure_id: string;
  definition_revision: number;
  kind: MeasureKind;
  timezone: string;
  period: CountPeriod | null;
  required_date: string;
  period_interval: PeriodInterval | null;
}
export type Observation = Wire.Observation;
export type InputObservation = Wire.InputObservation;
export type ExpressionValue = Wire.ExpressionValue;
export type MeasureValue = Wire.MeasureValue;
/** Normalization retains integer revision tokens and produces float value tokens for fingerprints. */
export type ManualWriteBody = NumericJSON<Wire.ManualWriteBody>;
export type ManualMeasureState = Wire.ManualMeasureState;
export type ManualWriteReceipt = Wire.ManualWriteReceipt;
export type Definitions = Map<string, Measure>;
export type Observations = Map<string, Observation>;
export const pair_key = (pair: MeasureDate): string => `${pair.measure_id}\0${pair.date}`;
