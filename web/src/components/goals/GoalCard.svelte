<script lang="ts">
 import {refreshAtBoundary,goalDateText} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import type {GoalResponse,ChartBlock} from '../../lib/goals/contracts';
 import {goalQueries} from '../../lib/goals/queries';
 import PageChart from '../pageKit/PageChart.svelte';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {goal,projectName=''}:{goal:GoalResponse;projectName?:string}=$props();
 const readings=createQuery(()=>goalQueries.readings(goal.id));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 let chart=$derived<ChartBlock|null>(goal.kind==='binary'?{type:'chart',id:'completion',kind:'binary',placement:'primary'}:goal.primary_chart);
</script>
<article class="goal-card" data-goal-card={goal.id}>
 <header class="card-head"><div class="goal-meta"><span>{projectName}</span>{#if projectName&&goal.deadline}<span aria-hidden="true">·</span>{/if}<span>{goal.deadline?`By ${goalDateText(goal.deadline)}`:'No deadline'}</span></div>
  <h2><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></h2>
 </header>
 <div class="card-progress">{#if chart}<PageChart block={chart} values={readings.data?.values??[]} goalId={goal.id} completed={goal.completed} status={goal.status} compact integrated/>{:else}<p>No readings yet</p>{/if}</div>
</article>
<style>
.goal-card{min-width:0;border:var(--border-hairline) solid var(--border-color);border-radius:var(--radius-lg);overflow:hidden;background:var(--surface-1)}.card-head{padding:17px 18px;min-height:114px;background:var(--surface-2)}.goal-meta{display:flex;align-items:center;gap:9px;font-size:12px;line-height:1.4;color:var(--text-faint)}.goal-meta span:first-child{color:var(--text-muted)}h2{margin:10px 0 0;min-height:55px;font-size:22px;font-weight:600;line-height:1.25;letter-spacing:-.025em}a{color:var(--text-strong);text-decoration:none}a:hover{color:var(--accent-bright)}.card-progress{padding:24px 20px}p{color:var(--text-muted)}@container (max-width:640px){.goal-meta{gap:7px}.card-head{min-height:0}h2{min-height:0}}
</style>
