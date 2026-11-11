<script lang="ts">
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import ManagerChecklistPreview from './ManagerChecklistPreview.svelte';
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import PageChart from '../pageKit/PageChart.svelte';
 import GoalRowState from './GoalRowState.svelte';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {goals}:{goals:GoalResponse[]}=$props();
 const sessions=createGoalRequestSessions();
 let onlyGoal=$derived(goals.length===1?goals[0]:null);
 let display=$derived(resolveGoalPrimaryDisplay(onlyGoal));
 let chart=$derived(display.chart);
 let sole=$derived(onlyGoal&&chart?onlyGoal:null);
 const readings=createQuery(()=>({...goalQueries.readings(sole?.id??''),enabled:Boolean(sole)}));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
</script>
{#if onlyGoal && display.checklist}
 {#key onlyGoal.id}<ManagerChecklistPreview initialGoal={onlyGoal} {sessions}/>{/key}
{:else if sole && chart}
 {#key sole.id}<PageChart block={{...chart,title:sole.title}} goalId={sole.id} values={readings.data?.values??[]} projection={readings.data?.charts?.[chart.id]} narrow supporting integrated compact status={sole.status} completed={sole.completed}/>{/key}
{:else}
 <div class="goal-list">{#each goals as goal (goal.id)}<a class="goal" data-manager-goal-id={goal.id} href={workspaceAddress({kind:'goal',id:goal.id})}><span class="goal-title">{goal.title}</span><GoalRowState {goal} preview/></a>{/each}</div>
{/if}
<style>
.goal-list{display:flex;flex-direction:column;gap:0}
.goal{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:var(--space-3);min-height:var(--rail-goal-row-height);font-size:var(--rail-goal-size);font-weight:var(--weight-body);line-height:var(--rail-row-line);color:var(--rail-tone-rest);text-decoration:none;border-radius:var(--radius-sm)}
.goal-title{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.goal:hover{color:var(--rail-tone-bright)}
.goal:focus-visible{outline:var(--focus-width) solid var(--focus-ring);outline-offset:var(--focus-offset)}
</style>
