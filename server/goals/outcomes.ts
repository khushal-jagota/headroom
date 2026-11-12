/** Persist achievement facts only when retained evidence proves their date. */
import type { TransactionSql } from '../core/database/contracts.ts';
import { PlannerError } from '../core/contracts.ts';
import { list_goals } from './data.ts';
import { evaluate_completion_date } from './rule_evaluation.ts';
export function reconcile_completions(sql: TransactionSql, now: number): number {
  let changed = 0;
  for (const goal of list_goals(sql)) {
    if (goal.completion_rule === null) continue;
    let date: string | null;
    try {
      date = evaluate_completion_date(sql, goal.completion_rule, now);
    } catch (e) {
      if (!(e instanceof PlannerError) && !(e instanceof TypeError)) throw e;
      console.error('goal completion evaluation failed', { goal_id: goal.id, error: e });
      continue;
    }
    if (date === goal.completion_date) continue;
    sql.run('UPDATE goals SET completion_date=?,revision=revision+1,updated_at=? WHERE id=?', [
      date,
      now,
      goal.id,
    ]);
    changed++;
  }
  return changed;
}
