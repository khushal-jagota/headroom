<script lang="ts">
 import type {GoalResponse} from '../../lib/goals/contracts';
 import GoalReading from './GoalReading.svelte';
 import {goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 /* A holder with one goal shows its status as a line of the holder's own header, so
  * the line belongs to the header row: it is washed, lit and clicked with it. */
 let {goal}:{goal:GoalResponse|null|undefined}=$props();
</script>
{#if goal}
<div class="sole-status" data-rail-motion={`row:${goal.id}`} data-goal-status={goal.id}><GoalReading {goal}/>{#if goal.deadline}<time datetime={goal.deadline} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>Due {goalDateText(goal.deadline)}</time>{:else}<span>No deadline</span>{/if}</div>
{/if}
<style>
.sole-status{display:flex;justify-content:space-between;gap:var(--space-2);font-size:var(--type-xs);margin-top:var(--space-1);color:var(--text-faintest)}
</style>
