<script lang="ts">
 import type {GoalRailStructure} from '../../lib/workspaceRailMotion';
 import NavigationWindowPages from '../NavigationWindowPages.svelte';
 import GoalReading from './GoalReading.svelte';
 import {goalDateText,deadlineTone} from '../../lib/goals/presentation';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {checklistCounts} from '../../lib/goals/checklistCompletion';
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
 // A goal with one Manager to itself is that Manager's goal: the same row as any other goal,
 // opening the Manager's page.
 let rows=$derived(sole?[sole]:visible);
 /** A checklist reads as how much of it is done; every other goal reads as its state. */
 function progress(goal:GoalResponse):string|null{
  const checklist=resolveGoalPrimaryDisplay(goal).checklist;
  if(!checklist||goal.status==='bad')return null;
  const {checked,total}=checklistCounts(checklist);
  return `${checked} of ${total} done`;
 }
</script>
{#snippet state(goal:GoalResponse)}
 <!-- One thing on the right, in the Small voice: "Below target" (or "Overdue") in red when the
      goal is off track; otherwise how far along it is ("2 of 6 done"); otherwise when it is
      due; otherwise its state in words. The rest stays in the row's accessible name, and the
      goal's preview and page show all of it. -->
 <span class="state"><GoalReading {goal}/>{#if progress(goal)}<span class="progress">{progress(goal)}</span>{/if}{#if goal.deadline}<time datetime={goal.deadline} data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} aria-label={`Due ${goalDateText(goal.deadline)}`}>{goalDateText(goal.deadline)}</time>{/if}</span>
{/snippet}
<div>
{#if rows.length||hasPager}
<section class="goals-rail" aria-label="Goals">
 {#each rows as goal (goal.id)}
  <!-- One line per goal in the rail's row voice: its name on the left, its state on the right. -->
  <div data-rail-motion={`row:${goal.id}`}>
  {#if sole}
   <a class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} data-sole-goal="" onclick={onDismiss}><span class="row-title">{goal.title}</span>{@render state(goal)}</a>
  {:else}
   <a class:selected={selectedId===goal.id} class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} aria-current={selectedId===goal.id?'page':undefined} aria-describedby={previewId===goal.id?`goal-preview-${goal.id}`:undefined} use:previewInput.row={{show:(target,immediate)=>onHover(target,goal.id,immediate),leave:onLeave}} onclick={onDismiss}><span class="row-title">{goal.title}</span>{@render state(goal)}</a>
   {#if previewId===goal.id}{#key goal.id}<GoalRailPreview id={goal.id} target={previewTarget} {rail} {instant} {sessions} bind:element {onKeepOpen} {onLeave}/>{/key}{/if}
  {/if}
  </div>
 {/each}
 {#if !sole}<NavigationWindowPages window={goalWindow} {onPage}/>{/if}
</section>
{/if}
</div>
<style>
/* A goal is part of its Manager's header: one compact line under the name, on the name's
   edge, never indented, in the Small voice. Its state sits at the right in the same size. Like
   every rail row it rests dim and brightens under the pointer or when its page is open; its
   hover and selected fills keep its own 6px radius. */
.goals-rail{display:flex;flex-direction:column;margin-top:var(--rail-row-gap)}
a{color:inherit;text-decoration:none}
.goal{box-sizing:border-box;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;column-gap:var(--space-3);height:var(--rail-goal-height);padding:0 var(--rail-pad);border-radius:var(--rail-goal-radius);touch-action:pan-y;font-size:var(--type-small);font-weight:var(--weight-small);line-height:16px;color:var(--tone-secondary);font-variant-numeric:tabular-nums;transition:color var(--motion-fast) var(--motion-ease)}
.row-title{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:clip}
.goal:hover,.selected{color:var(--tone-strong)}
.state{position:relative;display:flex;align-items:baseline;font-size:var(--type-small);font-weight:var(--weight-small);line-height:16px;color:var(--tone-secondary);font-variant-numeric:tabular-nums;white-space:nowrap}
/* Every word stays for assistive technology; only the one that matters most is drawn. */
.state > :global(*){position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.state:has(> :global([data-tone=bad])) > :global([data-tone=bad]),
.state:not(:has(> :global([data-tone=bad]))) > .progress,
.state:not(:has(> :global([data-tone=bad]), > .progress)) > time,
.state:not(:has(> :global([data-tone=bad]), > .progress, > time)) > :global([data-tone]){position:static;width:auto;height:auto;overflow:visible;clip-path:none}
.state :global([data-tone]){color:inherit}
.state :global([data-tone=bad]){color:var(--accent-error)}
time[data-deadline-tone=attention]{color:var(--accent-attention)}
.goal:focus-visible{outline:none}
</style>
