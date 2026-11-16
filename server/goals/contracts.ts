import type * as Wire from '../../web/src/lib/goals/contracts.ts';
import type { NumericJSON } from '../core/http/numeric_json.ts';
import type { FormulaExpression } from '../measures/contracts.ts';
export type PageDocument = NumericJSON<Wire.PageDocument>;
export type ChartBlock = NumericJSON<Wire.ChartBlock>;
export type ChecklistBlock = NumericJSON<Wire.ChecklistBlock>;
export type ChecklistItem = NumericJSON<Wire.ChecklistItem>;
export type Block = NumericJSON<Wire.Block>;
export type Tone = Wire.GoalResponse['status'];
export type GoalKind = Wire.GoalResponse['kind'];
export type GoalOutcome = Wire.GoalResponse['outcome'];
export type GoalOutcomeSummary = Wire.GoalOutcomeSummary;
export type GoalCompletionRule = Omit<Wire.GoalCompletionRule, 'actual' | 'target'> & {
  actual: FormulaExpression;
  target: FormulaExpression;
};
export type GoalResponse = NumericJSON<Wire.GoalResponse>;
export type GoalsResponse = NumericJSON<Wire.GoalsResponse>;
export type ScalarChartProjection = Wire.ScalarChartProjection;
export type ScalarChartPoint = Wire.ScalarChartPoint;
export type ChartProjection = Wire.ChartProjection;
export type CategoryChartProjection = Wire.CategoryChartProjection;
export type CategoryChartRange = Wire.CategoryChartRange;
export type CategoryChartValue = Wire.CategoryChartValue;
export type CategoryChartSummary = Wire.CategoryChartSummary;
export interface Goal {
  id: string;
  project_id: string;
  title: string;
  why: string;
  kind: GoalKind;
  active: boolean;
  binary_checked: boolean;
  primary_checklist_block_id: string | null;
  deadline: string | null;
  primary_chart: ChartBlock | null;
  page: PageDocument;
  revision: number;
  created_at: number;
  updated_at: number;
  employee_backend: string;
  employee_launch_model: string;
  employee_launch_reasoning_effort: string | null;
  completion_rule: GoalCompletionRule | null;
  completion_date: string | null;
  holder_kind: 'chief' | 'manager';
  holder_id: string | null;
}
export interface PageReadingSubject {
  id: string;
  page: PageDocument;
  revision: number;
  primary_chart?: ChartBlock | null;
  deadline?: string | null;
  kind?: GoalKind;
  binary_checked?: boolean;
  primary_checklist_block_id?: string | null;
}
export type ReadingSubject = Goal | PageReadingSubject;
