<script lang="ts">
 import {setContext,untrack} from 'svelte';
 import {refreshAtBoundary,goalDateText,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import {createQuery} from '@tanstack/svelte-query';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import {goalQueries} from '../../lib/goals/queries';
 import PageChart from '../pageKit/PageChart.svelte';
 import PageChecklist from '../pageKit/PageChecklist.svelte';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 import {createGoalEditor} from '../../lib/goals/editing';
 import type {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {createGoalPageContext} from '../../lib/goals/pageContext';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 let {goal,projectName='',sessions}:{goal:GoalResponse;projectName?:string;sessions:ReturnType<typeof createGoalRequestSessions>}=$props();
 const goalId=untrack(()=>goal.id);
 const editor=createGoalEditor(goalId);
 const readings=createQuery(()=>goalQueries.readings(goalId));
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 let display=$derived(resolveGoalPrimaryDisplay(goal));
 let chart=$derived(display.chart),checklist=$derived(display.checklist);
 setContext<PageContext>(PAGE_CONTEXT,createGoalPageContext({
  goalId:()=>goalId,goal:()=>goal,readings:()=>readings.data,narrow:()=>false,sessions:()=>sessions,
 },{updateBlock:editor.updateBlock}));
</script>
<!-- A yes-or-no goal has one fact to show, so its card is compact: the name with done or not
     beside it, and its line under them. -->
<article class="goal-card" class:compact={goal.kind==='binary' && !checklist} data-goal-card={goal.id}>
 <header class="card-head">
  <h2><a href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></h2>
  <div class="goal-meta"><span>{projectName}</span>{#if projectName}<span aria-hidden="true">·</span>{/if}<span data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>{goal.deadline?`Due ${goalDateText(goal.deadline)}`:'No deadline'}</span></div>
 </header>
 <div class="card-progress">{#if checklist}{#key checklist.id}<PageChecklist block={checklist} presentation="normal"/>{/key}{:else if chart}<PageChart block={chart} projection={readings.data?.charts?.[chart.id]} values={readings.data?.values??[]} goalId={goal.id} completed={goal.completed} status={goal.status} compact integrated/>{:else}<p>No readings yet</p>{/if}</div>
</article>
<style>
/* THE CARD (page system): a 1px edge at 7%, a 2% lift, radius 12, padding 20. The card's
   title is the section-head role; the line under it is small print: where the goal lives and
   when it is due. Its state is said in words inside the card, never drawn as a glyph. */
.goal-card{display:flex;flex-direction:column;min-width:0;border:var(--border-hairline) solid var(--page-card-edge);border-radius:var(--page-card-radius);overflow:hidden;background:var(--page-card-fill)}
.card-head{padding:var(--page-card-pad) var(--page-card-pad) 0}
.goal-meta{display:flex;align-items:center;flex-wrap:wrap;margin-top:2px;font-size:var(--type-small);font-weight:var(--weight-small);line-height:18px;color:var(--tone-tertiary);font-variant-numeric:tabular-nums}
.goal-meta [aria-hidden]{padding-inline:6px}
h2{margin:0;font-size:var(--type-page-section);font-weight:var(--weight-page-section);line-height:20px;letter-spacing:0}
a{color:var(--tone-primary);text-decoration:none;text-underline-offset:3px;text-decoration-thickness:1px}
a:hover{color:var(--tone-strong);text-decoration-line:underline;text-decoration-color:var(--tone-tertiary)}
.card-progress{flex:1;padding:var(--space-4) var(--page-card-pad) var(--page-card-pad)}
p{margin:0;color:var(--tone-tertiary);font-size:var(--type-small)}
/* Each card is as tall as what it holds; cards side by side align at the top. */
.card-progress{display:flex;flex-direction:column}.card-progress :global(.chart-range-footer){margin-top:0;padding-top:var(--space-4)}
/* The card is the checklist's surface: the checklist draws no surface or inset of its own. */
.card-progress:has(:global([data-block="checklist"])){padding:var(--space-4) var(--page-card-pad) var(--space-3)}
/* Inside the card the checklist stands on the card's own inset: its checks stand on the card's
   edge, and its fills reach 8px past the words on hover. */
.card-progress :global([data-block="checklist"]){border:0}
.card-progress :global([data-block="checklist"] .checklist-header){margin:-6px calc(var(--page-row-outset) * -1) var(--space-1)}
.card-progress :global([data-block="checklist"] .checklist-header .header){padding:6px var(--page-row-outset)}
.card-progress :global([data-block="checklist"] .body-inner){padding:0}
.card-progress :global([data-block="checklist"] .step-inner){padding:0}
.card-progress :global([data-block="checklist"] .group-toggle){padding:0 0 0 calc(var(--page-check-size) + var(--space-3));margin-inline:0}
/* A yes-or-no goal's card is the same card head as any goal's (title, its line, the inset)
   with its state beside the title, 12px after it on the title's baseline, and nothing under
   it: the state reads with the name it belongs to however wide the card is. */
.compact{display:grid;grid-template-columns:auto minmax(0,1fr);grid-template-areas:"title state" "meta meta";column-gap:var(--space-3);align-items:baseline;padding:var(--page-card-pad)}
.compact .card-head{display:contents}.compact .card-head h2{grid-area:title;min-width:0}.compact .card-head .goal-meta{grid-area:meta}
.compact .card-progress{grid-area:state;min-width:0;padding:0}
.card-progress :global([data-block="checklist"]){background:transparent;border-radius:0}
.compact .card-progress :global(.completion-headline){justify-content:flex-start}
</style>
