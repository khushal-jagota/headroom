<script lang="ts">
 import {setContext,untrack} from 'svelte';
 import {refreshAtBoundary,goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {goalQueries} from '../../lib/goals/queries';
 import PageChart from '../pageKit/PageChart.svelte';
 import PageChecklist from '../pageKit/PageChecklist.svelte';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 import {createGoalEditor} from '../../lib/goals/editing';
 import type {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {createGoalPageContext} from '../../lib/goals/pageContext';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {goal,projectName='',sessions}:{goal:GoalResponse;projectName?:string;sessions:ReturnType<typeof createGoalRequestSessions>}=$props();
 const goalId=untrack(()=>goal.id);
 const editor=createGoalEditor(goalId);
 const readings=createQuery(()=>goalQueries.readings(goalId));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 let display=$derived(resolveGoalPrimaryDisplay(goal));
 let chart=$derived(display.chart),checklist=$derived(display.checklist);
 setContext<PageContext>(PAGE_CONTEXT,createGoalPageContext({
  goalId:()=>goalId,goal:()=>goal,readings:()=>readings.data,narrow:()=>false,sessions:()=>sessions,
 },{updateBlock:editor.updateBlock}));
</script>
<article class="goal-card" data-goal-card={goal.id}>
 <header class="card-head">
  <h2><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></h2>
  <div class="goal-meta"><span>{projectName}</span>{#if projectName}<span aria-hidden="true">·</span>{/if}<span data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>{goal.deadline?`Due ${goalDateText(goal.deadline)}`:'No deadline'}</span></div>
 </header>
 <div class="card-progress">{#if checklist}{#key checklist.id}<PageChecklist block={checklist} presentation="normal"/>{/key}{:else if chart}<PageChart block={chart} projection={readings.data?.charts?.[chart.id]} values={readings.data?.values??[]} goalId={goal.id} completed={goal.completed} status={goal.status} compact integrated/>{:else}<p>No readings yet</p>{/if}</div>
</article>
<style>
.goal-card{display:flex;flex-direction:column;min-width:0;border:var(--border-hairline) solid var(--border-card);border-radius:var(--radius-lg);overflow:hidden;background:var(--surface-2)}.card-head{padding:var(--space-5) var(--space-5) 0}.goal-meta{display:flex;align-items:center;gap:6px;margin-top:2px;font-size:var(--type-xs);font-weight:500;line-height:16px;color:var(--text-faint)}.goal-meta span:first-child{color:var(--text-faint)}h2{margin:0;font-size:var(--type-lg);font-weight:640;line-height:22px;letter-spacing:-.01em}a{color:var(--text-strong);text-decoration:none;text-underline-offset:3px;text-decoration-thickness:1px}a:hover{text-decoration-line:underline;text-decoration-color:var(--text-faintest)}.card-progress{flex:1;padding:var(--space-4) var(--space-5) var(--space-5)}p{margin:0;color:var(--text-faint);font-size:var(--type-sm)}
/* The card is the checklist's surface; a second box inside it would only nest. */
/* A chart card stretches to its neighbour's height with the period switch on its floor, so
   the shared height reads as the card's own composition rather than empty space. */
.card-progress{display:flex;flex-direction:column}.card-progress > :global(.goal-chart-host){flex:1;display:flex;flex-direction:column}.card-progress :global(.goal-chart-host > .metric-card){flex:1;display:flex;flex-direction:column}.card-progress :global(.chart-range-footer){margin-top:auto;padding-top:var(--space-4)}
.card-progress:has(:global([data-block="checklist"])){padding:var(--space-1) 0 var(--space-2)}.card-progress :global([data-block="checklist"]){background:transparent;border-radius:0}
</style>
