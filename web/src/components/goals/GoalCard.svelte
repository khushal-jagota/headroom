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
 <header class="card-head"><div class="goal-meta"><span>{projectName}</span>{#if projectName&&goal.deadline}<span aria-hidden="true">·</span>{/if}<span data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>{goal.deadline?`By ${goalDateText(goal.deadline)}`:'No deadline'}</span></div>
  <h2><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></h2>
 </header>
 <div class="card-progress">{#if checklist}{#key checklist.id}<PageChecklist block={checklist} presentation="normal"/>{/key}{:else if chart}<PageChart block={chart} projection={readings.data?.charts?.[chart.id]} values={readings.data?.values??[]} goalId={goal.id} completed={goal.completed} status={goal.status} compact integrated/>{:else}<p>No readings yet</p>{/if}</div>
</article>
<style>
.goal-card{min-width:0;border:var(--border-hairline) solid var(--border-color);border-radius:var(--radius-lg);overflow:hidden;background:var(--surface-1)}.card-head{padding:17px 18px;min-height:114px;background:var(--surface-2)}.goal-meta{display:flex;align-items:center;gap:9px;font-size:12px;line-height:1.4;color:var(--text-faint)}.goal-meta span:first-child{color:var(--text-muted)}h2{margin:10px 0 0;min-height:55px;font-size:22px;font-weight:600;line-height:1.25;letter-spacing:-.025em}a{color:var(--text-strong);text-decoration:none}a:hover{color:var(--accent-bright)}.card-progress{padding:24px 20px}p{color:var(--text-muted)}@container (max-width:640px){.goal-meta{gap:7px}.card-head{min-height:0}h2{min-height:0}}
</style>
