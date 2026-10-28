<script lang="ts">
 import {getContext,onMount} from 'svelte';
 import {createRoot,type Root} from 'react-dom/client';
 import {createElement} from 'react';
 import {GoalChart} from '../../lib/charts/GoalChart';
 import type {ChartBlock,MeasureValue,GoalResponse} from '../../lib/goals/contracts';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 let {block,inset=false,values,goalId,narrow,completed,status,compact=false}:{block:ChartBlock;inset?:boolean;values?:MeasureValue[];goalId?:string;narrow?:boolean;completed?:boolean;status?:GoalResponse['status'];compact?:boolean}=$props();
 const context=getContext<PageContext|undefined>(PAGE_CONTEXT);
 let host:HTMLDivElement; let root=$state.raw<Root|null>(null);
 onMount(()=>{root=createRoot(host);return()=>root?.unmount();});
 $effect(()=>{root?.render(createElement(GoalChart,{block,values:values??context?.values??[],narrow:narrow??context?.narrow??false,preferenceKey:`goal-chart:${goalId??context?.goalId??'page'}:${block.id}`,completed:completed??context?.completed,status:status??context?.status,compact:compact||inset}));});
</script>
<div class="goal-chart-host" data-compact={compact} data-chart-id={block.id} bind:this={host}></div>
