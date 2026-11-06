<script lang="ts">
 import type {GoalRailStructure} from '../../lib/workspaceRailMotion';
 import NavigationWindowPages from '../NavigationWindowPages.svelte';
 import GoalReading from './GoalReading.svelte';
 import {goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {createGoalRailPreviewInput} from '../../lib/goals/railPreview';
 import GoalRailPreview from './GoalRailPreview.svelte';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {structure,onPage,selectedId=null,previewId=null,previewTarget=null,rail=null,instant=false,previewInput=createGoalRailPreviewInput(),element=$bindable<HTMLElement|null>(null),onHover=()=>undefined,onLeave=()=>undefined,onKeepOpen=()=>undefined,onDismiss=()=>undefined}:{
  structure:GoalRailStructure|undefined;onPage:(anchor:string,direction:'before'|'after')=>void;selectedId?:string|null;previewId?:string|null;previewTarget?:HTMLElement|null;rail?:HTMLElement|null;instant?:boolean;
  previewInput?:ReturnType<typeof createGoalRailPreviewInput>;element?:HTMLElement|null;
  onHover?:(target:HTMLElement,id:string,immediate:boolean)=>void;onLeave?:(target:HTMLElement)=>void;onKeepOpen?:()=>void;onDismiss?:()=>void;
 }=$props();
 const sessions=createGoalRequestSessions();
 let goalWindow=$derived(structure?.window);
 let sole=$derived(structure?.sole??null);
 let visible=$derived(structure?.rows??[]);
 let hasPager=$derived(Boolean(goalWindow&&(goalWindow.previous||goalWindow.next)));
</script>
<div>
{#if sole}
<div class="sole-status" data-rail-motion={`row:${sole.id}`} data-goal-status={sole.id}><GoalReading goal={sole}/>{#if sole.deadline}<time datetime={sole.deadline} style:color={deadlineColor(deadlineTone(sole.deadline,sole.completed,sole.status_date))}>Due {goalDateText(sole.deadline)}</time>{:else}<span>No deadline</span>{/if}</div>
{:else if visible.length||hasPager}
<section class="goals-rail" aria-label="Goals">
 {#each visible as goal (goal.id)}
  <div data-rail-motion={`row:${goal.id}`}><a class:selected={selectedId===goal.id} class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} aria-current={selectedId===goal.id?'page':undefined} aria-describedby={previewId===goal.id?`goal-preview-${goal.id}`:undefined} use:previewInput.row={{show:(target,immediate)=>onHover(target,goal.id,immediate),leave:onLeave}} onclick={onDismiss}><div class="row-heading"><span class="row-title">{goal.title}</span></div><small><GoalReading {goal}/>{#if goal.deadline}<time datetime={goal.deadline} data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>Due {goalDateText(goal.deadline)}</time>{:else}<span class="no-deadline" data-deadline-tone="neutral" style:color={deadlineColor('neutral')}>No deadline</span>{/if}</small></a>
  {#if previewId===goal.id}{#key goal.id}<GoalRailPreview id={goal.id} target={previewTarget} {rail} {instant} {sessions} bind:element {onKeepOpen} {onLeave}/>{/key}{/if}</div>
 {/each}
 <NavigationWindowPages window={goalWindow} {onPage}/>
</section>
{/if}
</div>
<style>
.sole-status{display:flex;justify-content:flex-start;gap:6px;font-size:var(--type-xs);line-height:16px;margin-top:0;color:var(--text-faintest)}
.goals-rail{margin-top:2px;font-size:var(--type-sm)}a{color:inherit;text-decoration:none}.goal{display:block;padding:6px var(--space-2);margin:0 calc(var(--space-2)*-1);border-radius:var(--radius-md);touch-action:pan-y;color:var(--text-muted)}.row-heading{display:flex;align-items:flex-start;gap:var(--space-2)}.row-title{flex:1;min-width:0;display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;line-height:20px;font-weight:590;overflow-wrap:anywhere}.goal:hover,.selected{color:var(--text-strong)}.goal small{display:flex;justify-content:flex-start;flex-wrap:wrap;gap:0 6px;margin-top:0;font-size:var(--type-xs);line-height:16px;color:var(--text-faintest)}.goal small :global(span[data-tone]:not([data-tone=bad]):not([data-tone=good]):not([data-tone=attention])),.sole-status :global(span[data-tone]:not([data-tone=bad]):not([data-tone=good]):not([data-tone=attention])){color:inherit}.goal small > :global(*) + :global(*)::before,.sole-status > :global(*) + :global(*)::before{content:"·";margin-right:6px;color:var(--text-faintest)}time,.no-deadline{color:var(--text-faintest);white-space:nowrap}.goal:focus-visible{outline:none}
</style>
