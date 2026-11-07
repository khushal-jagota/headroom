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
 /* The whole card opens its goal. The title stays the one link (the card's single tab stop);
    a press anywhere else on the card follows it, unless it lands on a control inside the card
    (a check, the period switch, Hide/Show, "N completed", a button, an inline edit, a link,
    the chart's explorer) or ends a text selection. */
 let titleLink=$state<HTMLAnchorElement|null>(null);
 const CONTROL='a[href],button,input,select,textarea,label,summary,[contenteditable]:not([contenteditable="false"]),[role="button"],[role="checkbox"],[role="slider"],[role="switch"],[role="tab"],[role="textbox"],[role="menu"],[role="menuitem"],[role="option"],[role="listbox"],[role="dialog"],[role="region"]';
 function openFromCard(event:MouseEvent){
  if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||!titleLink)return;
  const card=event.currentTarget as HTMLElement;
  for(const node of event.composedPath()){
   if(node===card)break;
   if(node instanceof Element&&node.matches(CONTROL))return;
  }
  if(!card.isConnected||(window.getSelection()?.toString()??'')!=='')return;
  titleLink.click();
 }
</script>
<!-- Every card hugs what it holds: its name and line, then its reading (a yes-or-no goal's is
     staging's glyph, word and context line). -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<article class="goal-card" data-goal-card={goal.id} onclick={openFromCard}>
 <header class="card-head">
  <h2><a bind:this={titleLink} href={workspaceAddress({kind:'goal',id:goal.id})}>{goal.title}</a></h2>
  <div class="goal-meta"><span>{projectName}</span>{#if projectName}<span aria-hidden="true">·</span>{/if}<span data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>{goal.deadline?`Due ${goalDateText(goal.deadline)}`:'No deadline'}</span></div>
 </header>
 <div class="card-progress">{#if checklist}{#key checklist.id}<PageChecklist block={checklist} presentation="normal"/>{/key}{:else if chart}<PageChart block={chart} projection={readings.data?.charts?.[chart.id]} values={readings.data?.values??[]} goalId={goal.id} completed={goal.completed} status={goal.status} compact integrated/>{:else}<p>No readings yet</p>{/if}</div>
</article>
<style>
/* THE CARD (page system): a 1px edge at 7%, a 2% lift, radius 12, padding 20. The card's
   title is the section-head role; the line under it is small print: where the goal lives and
   when it is due. */
.goal-card{display:flex;flex-direction:column;min-width:0;border:var(--border-hairline) solid var(--page-card-edge);border-radius:var(--page-card-radius);overflow:hidden;background:var(--page-card-fill);cursor:pointer}
/* The card answers the pointer as one target; the title underlines with it. */
/* The card does not light up under the pointer (owner); its title's underline says it opens. */
.goal-card:hover .card-head a{color:var(--tone-strong);text-decoration-line:underline;text-decoration-color:var(--tone-tertiary)}
.card-head{padding:var(--page-card-pad) var(--page-card-pad) 0}
.goal-meta{display:flex;align-items:center;flex-wrap:wrap;margin-top:2px;font-size:var(--type-small);font-weight:var(--weight-small);line-height:18px;color:var(--tone-tertiary);font-variant-numeric:tabular-nums}
.goal-meta [aria-hidden]{padding-inline:6px}
h2{margin:0;font-size:var(--type-page-section);font-weight:var(--weight-page-section);line-height:20px;letter-spacing:0}
a{color:var(--tone-primary);text-decoration:none;text-underline-offset:3px;text-decoration-thickness:1px}
a:hover{color:var(--tone-strong);text-decoration-line:underline;text-decoration-color:var(--tone-tertiary)}
.card-progress{padding:var(--space-4) var(--page-card-pad) var(--page-card-pad)}
p{margin:0;color:var(--tone-tertiary);font-size:var(--type-small)}
/* The card is the checklist's surface: the checklist draws no surface or inset of its own. */
.card-progress:has(:global([data-block="checklist"])){padding:var(--space-4) var(--page-card-pad) var(--space-3)}
/* Inside the card the checklist stands on the card's own inset: its checks stand on the card's
   edge, and its fills reach 8px past the words on hover. */
.card-progress :global([data-block="checklist"]){border:0}
.card-progress :global([data-block="checklist"] .checklist-header){margin:-6px calc(var(--page-row-outset) * -1) var(--space-1)}
.card-progress :global([data-block="checklist"] .checklist-header .header){padding:6px var(--page-row-outset)}
.card-progress :global([data-block="checklist"] .body-inner){padding:0}
.card-progress :global([data-block="checklist"] .step-inner){padding:var(--space-1) 0;margin-left:calc((var(--page-check-size) - var(--page-check-target)) / 2)}
.card-progress :global([data-block="checklist"] .group-toggle){padding:0 0 0 calc(var(--page-check-target) + var(--space-3) + (var(--page-check-size) - var(--page-check-target)) / 2);margin-inline:0}
.card-progress :global([data-block="checklist"]){background:transparent;border-radius:0}
</style>
