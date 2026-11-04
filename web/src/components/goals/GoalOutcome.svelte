<script lang="ts">
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {goalCompletionText,goalOutcomeText} from '../../lib/goals/presentation';
 let {goal}:{goal:Pick<GoalResponse,'completed'|'completion_date'|'outcome'>}=$props();
</script>

<div class="goal-outcome" aria-label="Completion and result">
 <span>{goalCompletionText(goal)}</span>
 <span class="result" data-outcome={goal.outcome}>{goalOutcomeText(goal.outcome)}</span>
</div>

<style>
 .goal-outcome{display:flex;align-items:baseline;flex-wrap:wrap;gap:var(--space-2) var(--space-4);font-size:12px;line-height:1.5;color:var(--text-faint)}
 .result{font-weight:500;color:var(--text-muted)}
 .result[data-outcome=hit]{color:var(--accent-done)}
 .result[data-outcome=miss]{color:var(--accent-error)}
</style>
