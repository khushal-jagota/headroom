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
<!-- A yes-or-no goal has one fact to show, so its card is a compact row-card: the name over
     its line on the left, done or not on the right. -->
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
/* Cards side by side share one height. A chart card puts the extra height above its period
   switch, which stands on the card's floor. */
.card-progress{display:flex;flex-direction:column}.card-progress > :global(.goal-chart-host){flex:1;display:flex;flex-direction:column}.card-progress :global(.goal-chart-host > .metric-card){flex:1;display:flex;flex-direction:column}.card-progress :global(.chart-range-footer){margin-top:auto;padding-top:var(--space-4)}
/* A checklist card spends its extra height evenly between its rows, so its last row
   ("2 completed") stands on the card's floor and no empty band forms under the list. */
.card-progress :global([data-block="checklist"]),.card-progress :global([data-block="checklist"] [data-checklist-body]),.card-progress :global([data-block="checklist"] .body),.card-progress :global([data-block="checklist"] .body-inner){flex:1;display:flex;flex-direction:column}
.card-progress :global([data-block="checklist"] .body-inner > .list){flex:1;display:flex;flex-direction:column;justify-content:space-between}
.card-progress :global([data-block="checklist"] .body-inner > .list::after){content:"";flex:none}
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
/* A yes-or-no goal's card is a quiet row-card, one column of the grid and 64px tall: its
   title over its line on the left, its state on the right, centred on the pair. */
.compact{display:grid;grid-template-columns:minmax(0,1fr) max-content;grid-template-areas:"title state" "meta state";column-gap:var(--space-4);align-items:center;padding:var(--page-row-card-pad-y) var(--page-row-card-pad-x)}
.compact .card-head{display:contents}.compact .card-head h2{grid-area:title;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.compact .card-head .goal-meta{grid-area:meta}
.compact .card-progress{grid-area:state;min-width:0;padding:0;white-space:nowrap}
.card-progress :global([data-block="checklist"]){background:transparent;border-radius:0}
.compact .card-progress :global(.completion-headline){justify-content:flex-end}
</style>
