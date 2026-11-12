import type { ExpressionValue } from '../../measures/contracts.ts';
import type { GoalCompletionRule } from '../contracts.ts';
export function rule_satisfied(
  rule: GoalCompletionRule,
  actual: ExpressionValue,
  target: ExpressionValue,
  day: string,
): boolean {
  if (
    actual.status !== 'value' ||
    target.status !== 'value' ||
    actual.value === null ||
    target.value === null ||
    !Number.isFinite(actual.value) ||
    !Number.isFinite(target.value) ||
    actual.missing_inputs.length ||
    target.missing_inputs.length
  )
    return false;
  if (
    !actual.inputs.some(
      (item) => item.value !== null && (item.run_id !== null || item.manual_write_id !== null),
    )
  )
    return false;
  if ([...actual.inputs, ...target.inputs].some((item) => item.date > day)) return false;
  return rule.direction === 'atmost' ? actual.value <= target.value : actual.value >= target.value;
}
