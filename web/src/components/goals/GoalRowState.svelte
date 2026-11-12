<script lang="ts">
 import GoalReading from './GoalReading.svelte';
 import {goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {checklistCounts} from '../../lib/goals/checklistCompletion';
 import type {GoalResponse} from '../../lib/goals/contracts';
 let {goal,preview=false}:{goal:GoalResponse;preview?:boolean}=$props();
 let counts=$derived.by(()=>{const checklist=resolveGoalPrimaryDisplay(goal).checklist;return checklist&&goal.status!=='bad'?checklistCounts(checklist):null;});
</script>
<small class="state" class:preview><GoalReading {goal}/>{#if counts}<span class="progress">{counts.checked} of {counts.total} done</span>{/if}{#if goal.deadline}<time datetime={goal.deadline} data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>Due {goalDateText(goal.deadline)}</time>{/if}</small>
<style>
.state{position:relative;display:flex;align-items:baseline;font-size:var(--rail-label-size);font-weight:var(--weight-body);line-height:16px;color:var(--rail-tone-label);font-variant-numeric:tabular-nums;white-space:nowrap}
.state > :global(*){position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.state:has(> :global([data-tone=bad])) > :global([data-tone=bad]),
.state:not(:has(> :global([data-tone=bad]))) > .progress,
.state:not(:has(> :global([data-tone=bad]), > .progress)) > time,
.state:not(:has(> :global([data-tone=bad]), > .progress, > time)) > :global([data-tone]){position:static;width:auto;height:auto;overflow:visible;clip-path:none}
.state :global([data-tone]){color:inherit}.state :global([data-tone=bad]){color:var(--accent-error)}
.state:not(.preview) :global([data-tone=attention]){color:var(--accent-attention)}
.state:not(.preview) :global([data-tone=good]){color:var(--accent-done)}
.preview time{color:var(--rail-tone-label)!important}
</style>
