<script lang="ts">
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalStatusText} from '../../lib/goals/presentation';
 import {chartData,chartHeadline,chartTone,formatReading} from '../../lib/charts/presentation';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {checklistCounts} from '../../lib/goals/checklistCompletion';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 let {goal,reading=false}:{goal:GoalResponse;reading?:boolean}=$props();
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
</script>
<span data-tone={tone}>{reading&&counts?`${counts.checked} done`:reading&&goal.kind==='quantitative'?(value===undefined?'—':`${formatReading(goal.primary_chart!,value)}`):status}</span>
<style>span{color:var(--text-muted)}span[data-tone=good]{color:var(--accent-done)}span[data-tone=bad]{color:var(--accent-error)}span[data-tone=attention]{color:var(--accent-attention,#d4ab6c)}</style>
