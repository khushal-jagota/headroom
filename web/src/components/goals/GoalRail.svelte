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
<!-- A Manager with one goal shows it as its own line: the goal's mark, its status and when it is due. -->
<div class="sole-status" data-rail-motion={`row:${sole.id}`} data-goal-status={sole.id}><GoalReading goal={sole} mark/><span class="sole-words"><GoalReading goal={sole}/>{#if sole.deadline}<time datetime={sole.deadline} style:color={deadlineColor(deadlineTone(sole.deadline,sole.completed,sole.status_date))}>Due {goalDateText(sole.deadline)}</time>{:else}<span>No deadline</span>{/if}</span></div>
{:else if visible.length||hasPager}
<section class="goals-rail" aria-label="Goals">
 {#each visible as goal (goal.id)}
  <!-- One line per goal, the rail's row voice: its name, then its mark and due date where a
       Ticket keeps its marks. The words for both stay in its accessible name. -->
  <div data-rail-motion={`row:${goal.id}`}><a class:selected={selectedId===goal.id} class="goal" href={workspaceAddress({kind:'goal',id:goal.id})} data-goal-id={goal.id} aria-current={selectedId===goal.id?'page':undefined} aria-describedby={previewId===goal.id?`goal-preview-${goal.id}`:undefined} use:previewInput.row={{show:(target,immediate)=>onHover(target,goal.id,immediate),leave:onLeave}} onclick={onDismiss}><span class="row-title">{goal.title}</span><small><GoalReading {goal} mark/><span class="sr-only"><GoalReading {goal}/></span>{#if goal.deadline}<time datetime={goal.deadline} data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))} aria-label={`Due ${goalDateText(goal.deadline)}`}>{goalDateText(goal.deadline)}</time>{:else}<span class="sr-only" data-deadline-tone="neutral">No deadline</span>{/if}</small></a>
  {#if previewId===goal.id}{#key goal.id}<GoalRailPreview id={goal.id} target={previewTarget} {rail} {instant} {sessions} bind:element {onKeepOpen} {onLeave}/>{/key}{/if}</div>
 {/each}
 <NavigationWindowPages window={goalWindow} {onPage}/>
</section>
{/if}
</div>
<style>
/* A goal is part of its Manager: it stands on the name's edge in the rail's row voice, one
   line like a Ticket, with its mark and due date in the trailing slot. Like every rail title
   it rests dim and brightens when hovered or open. */
.sole-status{display:flex;align-items:center;gap:var(--space-2);margin-top:2px;font-size:var(--type-xs);font-weight:500;line-height:16px;color:var(--rail-tone-label)}
.sole-words{display:flex;gap:6px;min-width:0}
.goals-rail{margin-top:var(--space-1);font-size:var(--type-ui)}
a{color:inherit;text-decoration:none}
.goal{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:var(--space-3);padding:5px var(--rail-row-pad);margin:0 calc(var(--rail-row-pad)*-1);border-radius:var(--radius-md);touch-action:pan-y;color:var(--rail-tone-rest);transition:color var(--motion-fast) var(--motion-ease)}
.row-title{min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;line-height:20px;font-weight:500}
.goal:hover,.selected{color:var(--text-strong)}
.goal small{display:flex;align-items:center;gap:6px;font-size:var(--type-xs);font-weight:500;line-height:16px;color:var(--rail-tone-label);font-variant-numeric:tabular-nums}
.goal small :global(span[data-tone]:not([data-tone=bad]):not([data-tone=good]):not([data-tone=attention])),.sole-status :global(span[data-tone]:not([data-tone=bad]):not([data-tone=good]):not([data-tone=attention])){color:inherit}
.sole-words > :global(*) + :global(*)::before{content:"·";margin-right:6px;color:var(--rail-tone-label)}
time{color:var(--rail-tone-label);white-space:nowrap}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.goal:focus-visible{outline:none}
</style>