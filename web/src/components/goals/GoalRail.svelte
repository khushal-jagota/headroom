<script lang="ts">
 import type {GoalRailStructure} from '../../lib/workspaceRailMotion';
 import NavigationWindowPages from '../NavigationWindowPages.svelte';
 import GoalRowState from './GoalRowState.svelte';
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
 // A goal with one Manager to itself is still a goal row like any other: it opens its goal,
 // has its preview and its selected state.
 let rows=$derived(sole?[sole]:visible);
</script>
<div>
{#if rows.length||hasPager}
<section class="goals-rail" aria-label="Goals">
 {#each rows as goal (goal.id)}
  <!-- A goal row: its name in the goal voice at the left, one state at the right. -->
  <div data-rail-motion={`row:${goal.id}`}>
  <a class:selected={selectedId===goal.id} class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} aria-current={selectedId===goal.id?'page':undefined} aria-describedby={previewId===goal.id?`goal-preview-${goal.id}`:undefined} use:previewInput.row={{show:(target,immediate)=>onHover(target,goal.id,immediate),leave:onLeave}} onclick={onDismiss}><span class="row-title">{goal.title}</span><GoalRowState {goal}/></a>
  {#if previewId===goal.id}{#key goal.id}<GoalRailPreview id={goal.id} initialGoal={goal} target={previewTarget} {rail} {instant} {sessions} bind:element {onKeepOpen} {onLeave}/>{/key}{/if}
  </div>
 {/each}
 {#if !sole}<NavigationWindowPages window={goalWindow} {onPage}/>{/if}
</section>
{/if}
</div>
<style>
/* A goal is part of its Manager's header: one line (owner), stacked tight under the name with no
   gap, on the rail's one text edge. Its name (14/400, ending in an ellipsis) at the left and one
   state at the right (12, the quiet label tone), in a 28px fill. Like every rail row it rests dim
   and brightens under the pointer or when its page is open. */
.goals-rail{display:flex;flex-direction:column;gap:0;margin-top:0}
a{color:inherit;text-decoration:none}
.goal{box-sizing:border-box;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;column-gap:var(--space-3);min-height:var(--rail-goal-row-height);padding:0 var(--rail-pad);border-radius:var(--radius-md);touch-action:pan-y;font-size:var(--rail-goal-size);font-weight:var(--weight-body);line-height:var(--rail-row-line);color:var(--rail-tone-rest);transition:color var(--motion-fast) var(--motion-ease)}
.row-title{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.goal:hover,.selected{color:var(--rail-tone-bright)}
.goal:focus-visible{outline:none}
</style>
