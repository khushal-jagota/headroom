<script lang="ts">
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalStatusText} from '../../lib/goals/presentation';
 import {chartData,chartHeadline,chartTone,formatReading} from '../../lib/charts/presentation';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {checklistCounts} from '../../lib/goals/checklistCompletion';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 let {goal,reading=false,mark=false}:{goal:GoalResponse;reading?:boolean;mark?:boolean}=$props();
 const readings=createQuery(()=>({...goalQueries.readings(goal.id),enabled:goal.kind==='quantitative'}));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 let current=$derived(goal.primary_chart?chartHeadline(goal.primary_chart,chartData(goal.primary_chart,readings.data?.values??[],'all',readings.data?.charts?.[goal.primary_chart.id]),null,readings.data?.charts?.[goal.primary_chart.id]):undefined);
 let value=$derived(current?.value),target=$derived(current?.target);
 let category=$derived(goal.primary_chart?.binding?current?.categorySummary:undefined);
 let checklist=$derived(resolveGoalPrimaryDisplay(goal).checklist);
 let counts=$derived(checklist?checklistCounts(checklist):null);
 let good=$derived(goal.primary_chart?.binding?category?.meets_expectation===true:goal.primary_chart?.expectation?current?.point?.meets_expectation===true:value!==undefined&&target!==undefined&&((goal.primary_chart?.direction??(goal.primary_chart?.kind==='time-budget'?'atmost':'atleast'))==='atmost'?value<=target:value>=target));
 let tone=$derived(goal.kind==='binary'?goal.status:goal.primary_chart?chartTone(goal.primary_chart,value,target,current?.point,category,current?.coverage_status):'neutral');
 let status=$derived.by(()=>{
  if(goal.kind==='binary')return goalStatusText(goal);
  if(value===undefined)return 'No current reading';
  if(goal.primary_chart?.binding&&(target===undefined||category?.meets_expectation==null))return category?.comparison?'Comparison unavailable':'No target';
  if(target===undefined)return goal.primary_chart?.expectation?'Expected reading unavailable':'No target';
  const within=(goal.primary_chart?.direction??(goal.primary_chart?.kind==='time-budget'?'atmost':'atleast'))==='atmost';
  return good?(within?'Within limit':'On target'):(within?'Above limit':'Below target');
 });
/* How far along the goal is, for the mark: a checklist's share done, a reading's share of
   its target, a yes-or-no goal's done or not. */
let share=$derived.by(()=>{
 if(counts)return counts.total?counts.checked/counts.total:0;
 if(goal.kind==='binary')return goal.completed?1:0;
 if(value===undefined||target===undefined||!target)return 0;
 return Math.max(0,Math.min(1,value/target));
});
/* The share as a filled wedge inside a ring, read clockwise from the top: a progress mark,
   never an arc that could pass for a spinner. */
let wedge=$derived.by(()=>{
 if(share<=0||share>=1)return '';
 const r=3.25,a=share*2*Math.PI,x=6+r*Math.sin(a),y=6-r*Math.cos(a);
 return `M6 6V${6-r}A${r} ${r} 0 ${share>.5?1:0} 1 ${x.toFixed(3)} ${y.toFixed(3)}Z`;
});
</script>
{#if mark}
<!-- The compact form for a goal's line: a ring filled as far as the goal has come, red when
     it is behind and amber when it is due. The words are its accessible name. -->
<span class="goal-mark" data-tone={tone} role="img" aria-label={status} title={status}><svg viewBox="0 0 12 12" aria-hidden="true"><circle class="ring" cx="6" cy="6" r="5"/>{#if share>=1}<circle class="fill" cx="6" cy="6" r="3.25"/>{:else if wedge}<path class="fill" d={wedge}/>{/if}</svg></span>
{:else}
<span data-tone={tone}>{reading&&counts?`${counts.checked} done`:reading&&goal.kind==='quantitative'?(value===undefined?'—':`${formatReading(goal.primary_chart!,value)}`):status}</span>
{/if}
<style>
.goal-mark{display:inline-flex;flex:none;width:12px;height:12px;color:var(--rail-tone-rest)}.goal-mark[data-tone=bad]{color:var(--accent-error)}.goal-mark[data-tone=attention]{color:var(--accent-attention,#d4ab6c)}.goal-mark[data-tone=good]{color:var(--accent-done)}.goal-mark svg{width:100%;height:100%}.ring{fill:none;stroke:currentColor;stroke-width:1;opacity:.8}.fill{fill:currentColor;stroke:none}
</style>
