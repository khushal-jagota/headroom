<script lang="ts">
 import {getContext,onMount} from 'svelte';
 import {createRoot,type Root} from 'react-dom/client';
 import {createElement} from 'react';
 import {ManualMeasurement} from '../../lib/charts/components/manual-measurements';
 import type {ManualMeasurementAdapter} from '../../lib/goals/manualMeasurements';
 import type {ManualWriteCommand,RequestSession} from '../../lib/goals/requestSessions';
 import {GoalChart} from '../../lib/charts/GoalChart';
 import type {ChartBlock,ChartProjection,MeasureValue,GoalResponse} from '../../lib/goals/contracts';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 let {block,supporting=false,inset=false,integrated=false,wide=false,projection,values,goalId,narrow,completed,status,compact=false,manualAdapter,manualSession}:{block:ChartBlock;supporting?:boolean;inset?:boolean;integrated?:boolean;wide?:boolean;values?:MeasureValue[];projection?:ChartProjection;goalId?:string;narrow?:boolean;completed?:boolean;status?:GoalResponse['status'];compact?:boolean;manualAdapter?:ManualMeasurementAdapter;manualSession?:RequestSession<ManualWriteCommand>}=$props();
 const context=getContext<PageContext|undefined>(PAGE_CONTEXT);
 let resolvedSession=$derived(manualSession??(block.measure_id?context?.manualSession?.(block.measure_id):undefined));
 let host:HTMLDivElement; let root=$state.raw<Root|null>(null);
 onMount(()=>{root=createRoot(host);return()=>root?.unmount();});
 $effect(()=>{if(block.manual&&!supporting){root?.render(createElement(ManualMeasurement,{key:`${goalId??context?.goalId??'page'}:${block.measure_id}:${block.manual.model}`,block,values:values??context?.values??[],narrow:narrow??context?.narrow??false,compact:compact||inset,integrated,preferenceKey:`goal-chart:${goalId??context?.goalId??'page'}:${block.id}`,session:resolvedSession,adapter:manualAdapter??(block.measure_id?context?.manualAdapter?.(block.measure_id):undefined)}));return;}root?.render(createElement(GoalChart,{block:supporting?{...block,placement:'supporting'}:block,projection:projection??context?.charts?.[block.id],values:values??context?.values??[],narrow:narrow??context?.narrow??false,preferenceKey:`goal-chart:${goalId??context?.goalId??'page'}:${block.id}`,completed:completed??context?.completed,status:status??context?.status,compact:compact||inset,integrated,wide}));});
</script>
<div class="goal-chart-host" data-compact={compact} data-chart-id={block.id} bind:this={host}></div>
