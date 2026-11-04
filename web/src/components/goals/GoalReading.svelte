<script lang="ts">
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalResultTone,goalStatusText} from '../../lib/goals/presentation';
 import {chartData,chartHeadline,chartTone,formatReading} from '../../lib/charts/presentation';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {checklistCounts} from '../../lib/goals/checklistCompletion';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 let {goal,reading=false}:{goal:GoalResponse;reading?:boolean}=$props();
 const readings=createQuery(()=>({...goalQueries.readings(goal.id),enabled:reading&&goal.kind==='quantitative'}));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 let current=$derived(goal.primary_chart?chartHeadline(goal.primary_chart,chartData(goal.primary_chart,readings.data?.values??[],'all',readings.data?.charts?.[goal.primary_chart.id]),null,readings.data?.charts?.[goal.primary_chart.id]):undefined);
 let value=$derived(current?.value),target=$derived(current?.target);
 let category=$derived(goal.primary_chart?.binding?current?.categorySummary:undefined);
 let checklist=$derived(resolveGoalPrimaryDisplay(goal).checklist);
 let counts=$derived(checklist?checklistCounts(checklist):null);
 let tone=$derived(reading&&goal.kind==='quantitative'
  ?goal.primary_chart?chartTone(goal.primary_chart,value,target,current?.point,category,current?.coverage_status):'neutral'
  :goalResultTone(goal));

</script>
<span data-goal-status={!reading?'':undefined} data-tone={tone}>{reading&&counts?`${counts.checked} done`:reading&&goal.kind==='quantitative'?(value===undefined?'—':`${formatReading(goal.primary_chart!,value)}`):goalStatusText(goal)}</span>
<style>span{color:var(--text-muted)}span[data-tone=good]{color:var(--accent-done)}span[data-tone=bad]{color:var(--accent-error)}span[data-tone=attention]{color:var(--accent-attention,#d4ab6c)}</style>
