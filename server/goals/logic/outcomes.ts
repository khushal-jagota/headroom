import type { Goal, GoalOutcome, GoalOutcomeSummary } from '../contracts.ts';
import { goal_completed } from './completion.ts';
export function goal_outcome(goal: Goal, today: string): GoalOutcome {
  if (goal.deadline === null) return 'undated';
  if (goal_completed(goal)) {
    if (goal.completion_date === null) return 'unknown';
    return goal.completion_date <= goal.deadline ? 'hit' : 'miss';
  }
  return goal.deadline < today ? 'miss' : 'pending';
}
export function outcome_summary(goals: Iterable<Goal>, today: string): GoalOutcomeSummary {
  let hits = 0,
    misses = 0;
  for (const goal of goals) {
    const outcome = goal_outcome(goal, today);
    if (outcome === 'hit') hits++;
    if (outcome === 'miss') misses++;
  }
  return { hits, misses, rate: hits + misses ? hits / (hits + misses) : null };
}
