<script lang="ts">
 import type {GoalRailStructure} from '../../lib/workspaceRailMotion';
 import NavigationWindowPages from '../NavigationWindowPages.svelte';
 import GoalReading from './GoalReading.svelte';
 import {goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import type {GoalResponse} from '../../lib/goals/contracts';
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
{#snippet state(goal:GoalResponse)}
 <!-- Staging's words, all of them visible: the goal's state, then when it is due ("Due Dec 8")
      or that it has no deadline. -->
 <small class="state"><GoalReading {goal}/>{#if goal.deadline}<time datetime={goal.deadline} data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>Due {goalDateText(goal.deadline)}</time>{:else}<span class="no-deadline" data-deadline-tone="neutral">No deadline</span>{/if}</small>
{/snippet}
<div>
{#if rows.length||hasPager}
<section class="goals-rail" aria-label="Goals">
 {#each rows as goal (goal.id)}
  <!-- A goal row: its name in the row voice, then its state and due date in the Small voice. -->
  <div data-rail-motion={`row:${goal.id}`}>
  <a class:selected={selectedId===goal.id} class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} aria-current={selectedId===goal.id?'page':undefined} aria-describedby={previewId===goal.id?`goal-preview-${goal.id}`:undefined} use:previewInput.row={{show:(target,immediate)=>onHover(target,goal.id,immediate),leave:onLeave}} onclick={onDismiss}><span class="row-title">{goal.title}</span>{@render state(goal)}</a>
  {#if previewId===goal.id}{#key goal.id}<GoalRailPreview id={goal.id} target={previewTarget} {rail} {instant} {sessions} bind:element {onKeepOpen} {onLeave}/>{/key}{/if}
  </div>
 {/each}
 {#if !sole}<NavigationWindowPages window={goalWindow} {onPage}/>{/if}
</section>
{/if}
</div>
<style>
/* A goal is part of its Manager's header: stacked tight under the name with no gap, on the
   rail's one text edge. Its name (14/400 on a 20px line) over staging's state and date line
   (12/400 on 16, in the quiet label tone), 6px inside its 48px fill. Like every rail row it rests dim and
   brightens under the pointer or when its page is open. */
.goals-rail{display:flex;flex-direction:column;gap:0;margin-top:0}
a{color:inherit;text-decoration:none}
.goal{box-sizing:border-box;display:block;padding:var(--rail-goal-pad-y) var(--rail-pad);border-radius:var(--radius-md);touch-action:pan-y;font-size:var(--rail-goal-size);font-weight:var(--weight-body);line-height:var(--rail-row-line);color:var(--rail-tone-rest);transition:color var(--motion-fast) var(--motion-ease)}
.row-title{position:relative;top:var(--rail-goal-title-nudge);display:-webkit-box;-webkit-line-clamp:2;line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;min-width:0;overflow-wrap:anywhere}
.goal:hover,.selected{color:var(--rail-tone-bright)}
.state{position:relative;top:var(--rail-goal-state-nudge);display:flex;justify-content:space-between;flex-wrap:wrap;gap:0 var(--space-2);font-size:var(--rail-label-size);font-weight:var(--weight-body);line-height:16px;color:var(--rail-tone-label);font-variant-numeric:tabular-nums}
.state :global([data-tone]){color:inherit}
.state :global([data-tone=bad]){color:var(--accent-error)}
time,.no-deadline{white-space:nowrap}
.goal:focus-visible{outline:none}
</style>
