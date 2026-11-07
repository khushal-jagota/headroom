<script lang="ts">
 import type {GoalRailStructure} from '../../lib/workspaceRailMotion';
 import NavigationWindowPages from '../NavigationWindowPages.svelte';
 import GoalReading from './GoalReading.svelte';
 import {goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
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
 // A goal with one Manager to itself is still a goal row like any other: it opens its goal,
 // has its preview and its selected state.
 let rows=$derived(sole?[sole]:visible);
 /** A checklist reads as how much of it is done (unless it is off track). */
 function progress(goal:GoalResponse):string|null{
  const checklist=resolveGoalPrimaryDisplay(goal).checklist;
  if(!checklist||goal.status==='bad')return null;
  const {checked,total}=checklistCounts(checklist);
  return `${checked} of ${total} done`;
 }
</script>
{#snippet state(goal:GoalResponse)}
 <!-- One line (owner): the name at the left and one thing at the right, in the Small voice:
      'Below target' (or 'Overdue') in red when the goal is off track; otherwise how far along a
      checklist is ('1 of 4 done'); otherwise when it is due ('Due Dec 8'); otherwise its state
      in words. Every word stays in the row for assistive technology; the goal's preview and
      page show all of it. -->
 <small class="state"><GoalReading {goal}/>{#if progress(goal)}<span class="progress">{progress(goal)}</span>{/if}{#if goal.deadline}<time datetime={goal.deadline} data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>Due {goalDateText(goal.deadline)}</time>{/if}</small>
{/snippet}
<div>
{#if rows.length||hasPager}
<section class="goals-rail" aria-label="Goals">
 {#each rows as goal (goal.id)}
  <!-- A goal row: its name in the goal voice at the left, one state at the right. -->
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
/* A goal is part of its Manager's header: one line (owner), stacked tight under the name with no
   gap, on the rail's one text edge. Its name (14/400, ending in an ellipsis) at the left and one
   state at the right (12, the quiet label tone), in a 28px fill. Like every rail row it rests dim
   and brightens under the pointer or when its page is open. */
.goals-rail{display:flex;flex-direction:column;gap:0;margin-top:0}
a{color:inherit;text-decoration:none}
.goal{box-sizing:border-box;display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;column-gap:var(--space-3);min-height:var(--rail-goal-row-height);padding:0 var(--rail-pad);border-radius:var(--radius-md);touch-action:pan-y;font-size:var(--rail-goal-size);font-weight:var(--weight-body);line-height:var(--rail-row-line);color:var(--rail-tone-rest);transition:color var(--motion-fast) var(--motion-ease)}
.row-title{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.goal:hover,.selected{color:var(--rail-tone-bright)}
.state{position:relative;display:flex;align-items:baseline;font-size:var(--rail-label-size);font-weight:var(--weight-body);line-height:16px;color:var(--rail-tone-label);font-variant-numeric:tabular-nums;white-space:nowrap}
/* Every word stays for assistive technology; only the one that matters most is drawn. */
.state > :global(*){position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
.state:has(> :global([data-tone=bad])) > :global([data-tone=bad]),
.state:not(:has(> :global([data-tone=bad]))) > .progress,
.state:not(:has(> :global([data-tone=bad]), > .progress)) > time,
.state:not(:has(> :global([data-tone=bad]), > .progress, > time)) > :global([data-tone]){position:static;width:auto;height:auto;overflow:visible;clip-path:none}
.state :global([data-tone]){color:inherit}
.state :global([data-tone=bad]){color:var(--accent-error)}
/* Owner: the goal's state carries its colour in the sidebar too: red off track, yellow needing
   attention, green on track. */
.state :global([data-tone=attention]){color:var(--accent-attention)}
.state :global([data-tone=good]){color:var(--accent-done)}
.goal:focus-visible{outline:none}
</style>
