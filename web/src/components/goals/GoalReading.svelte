<script lang="ts">
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalStatusText} from '../../lib/goals/presentation';
 import {chartData,chartHeadline,chartTone,formatReading} from '../../lib/charts/presentation';
 import type {GoalResponse} from '../../lib/goals/contracts';
 let {goal,reading=false}:{goal:GoalResponse;reading?:boolean}=$props();
 const readings=createQuery(()=>({...goalQueries.readings(goal.id),enabled:goal.kind==='quantitative'}));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 let current=$derived(goal.primary_chart?chartHeadline(goal.primary_chart,chartData(goal.primary_chart,readings.data?.values??[],'all')):undefined);
 let value=$derived(current?.value),target=$derived(current?.target);
 let good=$derived(value!==undefined&&target!==undefined&&((goal.primary_chart?.direction??(goal.primary_chart?.kind==='time-budget'?'atmost':'atleast'))==='atmost'?value<=target:value>=target));
 let tone=$derived(goal.kind==='binary'?goal.status:goal.primary_chart?chartTone(goal.primary_chart,value,target):'neutral');
 let status=$derived(goal.kind==='binary'?goalStatusText(goal):value===undefined?'No current reading':target===undefined?'No target':good?((goal.primary_chart?.direction??(goal.primary_chart?.kind==='time-budget'?'atmost':'atleast'))==='atmost'?'Within limit':'On target'):((goal.primary_chart?.direction??(goal.primary_chart?.kind==='time-budget'?'atmost':'atleast'))==='atmost'?'Above limit':'Below target'));
</script>
<span data-tone={tone}>{reading&&goal.kind==='quantitative'?(value===undefined?'—':formatReading(goal.primary_chart!,value)):status}</span>
<style>span{color:var(--text-muted)}span[data-tone=good]{color:var(--accent-done)}span[data-tone=bad]{color:var(--accent-error)}span[data-tone=attention]{color:var(--accent-attention,#d4ab6c)}</style>
