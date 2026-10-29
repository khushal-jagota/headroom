<script lang="ts">
 import {getContext,onMount} from 'svelte';
 import {createRoot,type Root} from 'react-dom/client';
 import {createElement} from 'react';
 import {ManualMeasurement} from '../../lib/charts/components/manual-measurements';
 import type {ManualMeasurementAdapter} from '../../lib/goals/manualMeasurements';
 import {GoalChart} from '../../lib/charts/GoalChart';
 import type {ChartBlock,ScalarChartProjection,MeasureValue,GoalResponse} from '../../lib/goals/contracts';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 let {block,inset=false,integrated=false,projection,values,goalId,narrow,completed,status,compact=false,manualAdapter}:{block:ChartBlock;inset?:boolean;integrated?:boolean;values?:MeasureValue[];projection?:ScalarChartProjection;goalId?:string;narrow?:boolean;completed?:boolean;status?:GoalResponse['status'];compact?:boolean;manualAdapter?:ManualMeasurementAdapter}=$props();
 const context=getContext<PageContext|undefined>(PAGE_CONTEXT);
 let host:HTMLDivElement; let root=$state.raw<Root|null>(null);
 onMount(()=>{root=createRoot(host);return()=>root?.unmount();});
 $effect(()=>{if(block.manual){root?.render(createElement(ManualMeasurement,{key:`${block.measure_id}:${block.manual.model}`,block,values:values??context?.values??[],narrow:narrow??context?.narrow??false,compact:compact||inset,integrated,preferenceKey:`goal-chart:${goalId??context?.goalId??'page'}:${block.id}`,adapter:manualAdapter??(block.measure_id?context?.manualAdapter?.(block.measure_id):undefined)}));return;}root?.render(createElement(GoalChart,{block,projection:projection??context?.charts?.[block.id],values:values??context?.values??[],narrow:narrow??context?.narrow??false,preferenceKey:`goal-chart:${goalId??context?.goalId??'page'}:${block.id}`,completed:completed??context?.completed,status:status??context?.status,compact:compact||inset,integrated}));});
</script>
<div class="goal-chart-host" data-compact={compact} data-chart-id={block.id} bind:this={host}></div>
