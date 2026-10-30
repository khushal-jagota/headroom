<script lang="ts">
 import {onDestroy,untrack} from 'svelte';
 import {viewWindow} from '../../lib/navigation/views/owner';
 import {navigationWindow} from '../../lib/navigation/presentation';
 import NavigationWindowPages from '../NavigationWindowPages.svelte';
 import GoalReading from './GoalReading.svelte';
 import ConversationSignalMark from '../ConversationSignalMark.svelte';
 import {navigationApplication} from '../../lib/navigation/application.svelte';
 import {navigationConversationSignal} from '../../lib/navigation/presentation';
 const navigation=navigationApplication();
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {refreshAtBoundary,goalDateText} from '../../lib/goals/presentation';
 import {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {createGoalRailPreviewInput} from '../../lib/goals/railPreview';
 import GoalRailPreview from './GoalRailPreview.svelte';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {selectedId=null,previewId=null,previewTarget=null,rail=null,instant=false,previewInput=createGoalRailPreviewInput(()=>previewTarget),element=$bindable<HTMLElement|null>(null),onHover=()=>undefined,onLeave=()=>undefined,onKeepOpen=()=>undefined,onDismiss=()=>undefined}:{
  selectedId?:string|null;previewId?:string|null;previewTarget?:HTMLElement|null;rail?:HTMLElement|null;instant?:boolean;
  previewInput?:ReturnType<typeof createGoalRailPreviewInput>;element?:HTMLElement|null;
  onHover?:(target:HTMLElement,id:string,immediate:boolean)=>void;onLeave?:(target:HTMLElement)=>void;onKeepOpen?:()=>void;onDismiss?:()=>void;
 }=$props();
 const sessions=createGoalRequestSessions();
 const goals=createQuery(()=>goalQueries.list());
 let page=$state<{anchor:string;direction:'before'|'after'}|null>(null);
 let presentation=$derived(navigation?.selectors.effective?navigation.presentationFor(navigation.selectors.effective):null);
 let goalWindow=$derived(navigationWindow(presentation,'goals'));
 let visible=$derived((goalWindow?.members??[]).flatMap(member=>{const goal=goals.data?.goals.find(goal=>goal.id===member.id);return goal?.active?[goal]:[]}));
 $effect.pre(()=>{if(previewId&&goals.data&&!visible.some(goal=>goal.id===previewId))untrack(onDismiss);});
 $effect(()=>{navigation?.owner.setDemand('goal-rail',{windows:[viewWindow('goals',null,{limit:60,...page})]});});
 onDestroy(()=>navigation?.owner.setDemand('goal-rail',undefined));
 $effect(()=>{if(goals.data)return refreshAtBoundary(goals.data.next_refresh_at,()=>void goals.refetch());});
</script>
<section class="goals-rail" aria-label="Goals">
 {#each visible as goal (goal.id)}
  <a class:selected={selectedId===goal.id} class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} aria-current={selectedId===goal.id?'page':undefined} aria-describedby={previewId===goal.id?`goal-preview-${goal.id}`:undefined} use:previewInput.row={{show:(target,immediate)=>onHover(target,goal.id,immediate),leave:onLeave}} onclick={onDismiss}><div class="row-heading"><span class="row-title">{goal.title}</span><ConversationSignalMark signal={navigationConversationSignal(navigation?.selectors.effective,goal.conversation_id)} conversationId={goal.conversation_id}/></div><small><GoalReading {goal}/>{#if goal.deadline}<time datetime={goal.deadline}>Due {goalDateText(goal.deadline)}</time>{:else}<span class="no-deadline">No deadline</span>{/if}</small></a>
  {#if previewId===goal.id}{#key goal.id}<GoalRailPreview id={goal.id} target={previewTarget} {rail} {instant} {sessions} bind:element {onKeepOpen} {onLeave}/>{/key}{/if}
 {:else}{#if goalWindow}<p>No active goals</p>{/if}{/each}
 <NavigationWindowPages window={goalWindow} onPage={(anchor,direction)=>page={anchor,direction}}/>
</section>
<style>
.goals-rail{margin-top:var(--space-5);font-size:var(--type-sm)}a{color:inherit;text-decoration:none}.goal{display:block;padding:var(--space-2);margin:0 calc(var(--space-2)*-1);border-radius:var(--radius-sm);touch-action:pan-y}.row-heading{display:flex;align-items:flex-start;gap:var(--space-2)}.row-title{flex:1;min-width:0;display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:1.45;overflow-wrap:anywhere}.goal:hover,.selected{background:var(--surface-2)}.goal small{display:flex;justify-content:space-between;flex-wrap:wrap;gap:var(--space-1) var(--space-2);margin-top:var(--space-1);font-size:var(--type-xs);color:var(--text-muted)}time,.no-deadline{color:var(--text-faintest);white-space:nowrap}p{color:var(--text-faint);font-size:var(--type-xs)}.goal:focus-visible{outline:var(--border-strong) solid var(--accent-bright);outline-offset:1px}
</style>
