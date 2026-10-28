<script lang="ts">
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import type {GoalResponse,ChartBlock} from '../../lib/goals/contracts';
 import {goalQueries} from '../../lib/goals/queries';
 import PageChart from '../pageKit/PageChart.svelte';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {goal}:{goal:GoalResponse}=$props();
 const readings=createQuery(()=>goalQueries.readings(goal.id));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 let chart=$derived<ChartBlock|null>(goal.kind==='binary'?{type:'chart',id:'completion',kind:'binary',placement:'primary'}:goal.primary_chart);
</script>
<article class="goal-card" data-goal-card={goal.id}><h2><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></h2>
 {#if chart}<PageChart block={chart} values={readings.data?.values??[]} goalId={goal.id} completed={goal.completed} status={goal.status} compact />{:else}<p>No readings yet</p>{/if}
 <footer><span>{goal.deadline?`Due ${goal.deadline}`:'No deadline'}</span><span>{goal.active?'Active':'Inactive'}</span></footer>
</article>
<style>
.goal-card{min-width:0;border:var(--border-hairline) solid var(--border-color);border-radius:var(--radius-md);background:var(--surface-1);padding:22px}h2{margin:0 0 var(--space-5);font-size:16px;font-weight:500;line-height:1.4}a{color:var(--text-strong);text-decoration:none}a:hover{color:var(--accent-bright)}footer{display:flex;justify-content:space-between;gap:var(--space-3);margin-top:var(--space-4);color:var(--text-faint);font-size:12px}p{color:var(--text-muted)}
</style>
