<script lang="ts">
 import {onDestroy} from 'svelte';
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
 import {goalStatusText,refreshAtBoundary} from '../../lib/goals/presentation';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {selectedId=null}:{selectedId?:string|null}=$props();
 const goals=createQuery(()=>goalQueries.list());
 let page=$state<{anchor:string;direction:'before'|'after'}|null>(null);
 let presentation=$derived(navigation?.selectors.effective?navigation.presentationFor(navigation.selectors.effective):null);
 let goalWindow=$derived(navigationWindow(presentation,'goals'));
 let visible=$derived((goalWindow?.members??[]).flatMap(member=>{const goal=goals.data?.goals.find(goal=>goal.id===member.id);return goal?.active?[goal]:[]}));
 $effect(()=>{navigation?.owner.setDemand('goal-rail',{windows:[viewWindow('goals',null,{limit:60,...page})]});});
 onDestroy(()=>navigation?.owner.setDemand('goal-rail',undefined));
 $effect(()=>{if(goals.data)return refreshAtBoundary(goals.data.next_refresh_at,()=>void goals.refetch());});
</script>
<div class="goals-rail">
 <div class="heading">Active goals <a href={workspaceAddress({kind:'goals'})}>All goals</a></div>
 {#each visible as goal (goal.id)}
  <a class:selected={selectedId===goal.id} class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} aria-current={selectedId===goal.id?'page':undefined}><span>{goal.title}</span><ConversationSignalMark signal={navigationConversationSignal(navigation?.selectors.effective,goal.conversation_id)} conversationId={goal.conversation_id}/><small><GoalReading {goal}/>{#if goal.deadline}<time datetime={goal.deadline}>Due {new Date(goal.deadline+'T12:00:00Z').toLocaleDateString('en-US',{month:'short',day:'numeric',timeZone:'UTC'})}</time>{/if}</small></a>
 {:else}{#if goalWindow}<p>No active goals</p>{/if}{/each}
 <NavigationWindowPages window={goalWindow} onPage={(anchor,direction)=>page={anchor,direction}}/>
</div>
<style>
.goals-rail{margin-top:var(--space-5);font-size:var(--type-sm)}.heading{display:flex;align-items:center;justify-content:space-between;color:var(--text-faint);font-size:var(--type-xs);margin-bottom:var(--space-2)}a{color:inherit;text-decoration:none}.heading a{color:var(--text-muted)}.goal{display:block;padding:var(--space-2);margin:0 calc(var(--space-2)*-1);border-radius:var(--radius-sm)}.goal:hover,.selected{background:var(--surface-2)}.goal small{display:flex;justify-content:space-between;gap:8px;margin-top:var(--space-1);font-size:var(--type-xs);color:var(--text-muted)}time{color:var(--text-faintest);white-space:nowrap}p{color:var(--text-faint);font-size:var(--type-xs)}
</style>
