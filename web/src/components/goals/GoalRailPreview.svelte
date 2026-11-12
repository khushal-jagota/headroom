<script lang="ts">
 import {setContext,untrack} from 'svelte';
 import {skipPreviewTabStops} from '../../lib/previewTabOrder';
 import {createQuery} from '@tanstack/svelte-query';
 import {queries} from '../../lib/queryCatalogue';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalDateText,refreshAtBoundary,deadlineTone,deadlineColor} from '../../lib/goals/presentation';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {createGoalPageContext} from '../../lib/goals/pageContext';
 import {createGoalEditor} from '../../lib/goals/editing';
 import type {GoalResponse} from '../../lib/goals/contracts';
 import type {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {goalPreviewPosition,GOAL_PREVIEW_WIDTH,GOAL_PREVIEW_GUTTER} from '../../lib/goals/railPreview';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 import PageChart from '../pageKit/PageChart.svelte';
 import PageChecklist from '../pageKit/PageChecklist.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {id,initialGoal,target,rail,instant=false,sessions,onKeepOpen=()=>undefined,onLeave=()=>undefined,element=$bindable<HTMLElement|null>(null)}:{
  id:string;initialGoal:GoalResponse;target:HTMLElement|null;rail:HTMLElement|null;instant?:boolean;sessions:ReturnType<typeof createGoalRequestSessions>;
  onKeepOpen?:()=>void;onLeave?:(target:HTMLElement)=>void;element?:HTMLElement|null;
 }=$props();
 // The parent keys this component by goal identity. Writes keep this captured ID.
 const goalId=untrack(()=>id);
 // A switch stays instant for this panel. Resetting the shared hover flag must not
 // restart its entrance animation two frames later.
 const enterInstant=untrack(()=>instant);
 const anchor=untrack(()=>target);
 const editor=createGoalEditor(goalId);
 const detail=createQuery(()=>goalQueries.detail(goalId)),readings=createQuery(()=>goalQueries.readings(goalId));
 const projects=createQuery(()=>queries.projects());
 // The rail already owns this full goal. Use it until the detail read arrives.
 let goal=$derived(detail.data??initialGoal);
 let project=$derived(projects.data?.projects.find(project=>project.id===goal?.project_id));
 let display=$derived(resolveGoalPrimaryDisplay(goal));
 let checklist=$derived(display.checklist);
 let binarySession=$derived(goal?.kind==='binary'&&!checklist?sessions.binary(goalId):null);
 let receiptVersion=$state(0);
 $effect(()=>binarySession?.subscribe(()=>receiptVersion++));
 let binaryReceipt=$derived.by(()=>{receiptVersion;return binarySession?.current;});
 let chart=$derived(display.chart);
 setContext<PageContext>(PAGE_CONTEXT,createGoalPageContext({
  goalId:()=>goalId,goal:()=>goal,readings:()=>readings.data,narrow:()=>true,sessions:()=>sessions,
 },{
  updateBlock:editor.updateBlock,
  manualSession:(measureId)=>sessions.manual(goalId,measureId)
 }));
 $effect(()=>{if(goal)return refreshAtBoundary(Math.min(goal.next_refresh_at,readings.data?.next_refresh_at??Infinity),()=>{void detail.refetch();void readings.refetch();});});
 let binarySubmissionError=$state<unknown>(null);
 async function complete(completed:boolean){
  binarySubmissionError=null;
  const session=binarySession;
  if(!session)return;
  try{await session.run({completed},()=>editor.write({completed,expected_revision:goal.revision}));}
  catch(error){if(!session.current)binarySubmissionError=error;}
 }
 let left=$state(0),top=$state(0),availableBottom=$state<number|null>(null);
 $effect(()=>{
  if(!element||!target||!rail)return;
  const panel=element,anchor=target,currentRail=rail;
  const place=()=>{
   const railRect=currentRail.getBoundingClientRect();
   // The compact rail ends above the persistent destination bar.
   const bottom=Math.min(window.innerHeight,railRect.bottom);
   availableBottom=bottom;
   const height=Math.min(panel.offsetHeight,Math.max(0,bottom-GOAL_PREVIEW_GUTTER*2));
   const position=goalPreviewPosition(anchor.getBoundingClientRect(),railRect.right,panel.offsetWidth,height,window.innerWidth,bottom);
   left=position.left;top=position.top;
  };
  const observer=new ResizeObserver(place);
  observer.observe(panel);observer.observe(anchor);observer.observe(currentRail);place();
  window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
  return()=>{observer.disconnect();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);};
 });
</script>
<!-- Native Tab continues to the next sidebar row. Preview controls remain available to the pointer. -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions (The section only stops propagation after native control handlers. It performs no click action.) -->
<section bind:this={element} use:skipPreviewTabStops data-goal-preview={goalId} data-goal-preview-panel id={`goal-preview-${goalId}`} class="goal-preview" class:instant={enterInstant} style:left={`${left}px`} style:top={`${top}px`} style:--preview-width={`${GOAL_PREVIEW_WIDTH}px`} style:--preview-gutters={`${GOAL_PREVIEW_GUTTER*2}px`} style:--preview-bottom={availableBottom===null?'100dvh':`${availableBottom}px`} aria-label={`Goal preview: ${goal?.title??'Goal'}`} onclick={event=>event.stopPropagation()} onpointerenter={onKeepOpen} onpointerleave={event=>{if(event.pointerType!=='touch'&&anchor)onLeave(anchor);}} onfocusin={onKeepOpen} onfocusout={event=>{if(event.relatedTarget&&!element?.contains(event.relatedTarget as Node)&&event.relatedTarget!==anchor&&anchor)onLeave(anchor);}}>
 {#if goal}
  <header class="preview-header"><div class="preview-date">{#if project}<span class="preview-project">{project.name}</span><span aria-hidden="true">·</span>{/if}<span data-deadline-tone={deadlineTone(goal.deadline,goal.completed,goal.status_date)} style:color={deadlineColor(deadlineTone(goal.deadline,goal.completed,goal.status_date))}>{goal.deadline?`Due ${goalDateText(goal.deadline)}`:'No deadline'}</span></div><h1>{goal.title}</h1></header>
  {#if checklist}<div class="preview-checklist"><PageChecklist block={checklist} presentation="small"/></div>
  {:else if chart}<div class="primary-chart"><PageChart block={chart} compact integrated/>
   {#if goal.kind==='binary'}<div class="panel-actions"><button class="completion-action" disabled={binaryReceipt?.status==='pending'} onclick={()=>complete(!goal!.completed)}>{binaryReceipt?.status==='pending'?'Saving…':goal.completed?'Mark not done':'Mark done'}</button></div>{/if}
  </div>{/if}
  {#if binaryReceipt?.status==='failed'}<div class="preview-error"><ErrorLine error={binaryReceipt.error}/><button onclick={()=>complete(binaryReceipt!.command.completed)}>Retry save</button><button onclick={()=>binarySession?.discard()}>Discard</button></div>{/if}
  {#if binarySubmissionError}<div class="preview-error"><ErrorLine error={binarySubmissionError}/></div>{/if}
  {#if detail.error}<div class="preview-error"><ErrorLine error={detail.error}/></div>{/if}
  {#if readings.error}<div class="preview-error"><ErrorLine error={readings.error}/></div>{/if}
 {/if}
</section>
<style>
 /* Staging's goal preview, in today's colours: the rail's float surface (#2a2a2a and a soft
    shadow, no ring), the header on a band one step lighter, the chart or checklist below it.
    The checklist draws no card of its own: it stands on the preview's 16px inset on both sides,
    its checks on the header's text edge. Buttons are filled, never outlined, and keep staging's
    size: the 1px edge they lost is given back as padding. */
 .goal-preview{position:fixed;z-index:40;width:min(var(--preview-width),calc(100vw - var(--preview-gutters)));max-height:calc(var(--preview-bottom) - var(--preview-gutters));overflow:auto;overscroll-behavior:contain;scrollbar-width:thin;border:0;border-radius:var(--radius-lg);background:var(--rail-preview-surface);box-shadow:var(--rail-preview-shadow);font-size:var(--type-sm);pointer-events:auto;animation:goal-preview-in var(--motion-base) var(--motion-ease)}
 .goal-preview.instant{animation:none}.preview-header{position:relative;background:var(--rail-preview-band);padding:var(--space-4) var(--space-4) 0}.preview-date{display:flex;align-items:center;flex-wrap:wrap;gap:9px;font-size:12px;line-height:1.4;color:var(--text-faint)}.preview-project{color:var(--text-muted)}h1{margin:var(--space-2) 0 0;font-size:var(--type-lg);line-height:1.35;font-weight:600;letter-spacing:var(--tracking-title);color:var(--text-strong);overflow-wrap:anywhere}.primary-chart{padding:var(--space-4) var(--space-4) var(--space-4)}.panel-actions{display:flex;justify-content:flex-end;margin-top:var(--space-4)}.completion-action{border:0;border-radius:var(--radius-md);background:var(--button-secondary-fill);padding:9px 16px;color:var(--tone-primary);font:inherit;font-size:13px;cursor:pointer}.completion-action:hover{background:var(--button-secondary-fill-hover);color:var(--text-strong)}.preview-error{padding:var(--space-4)}.preview-error button{font:inherit;color:var(--tone-primary);background:var(--button-secondary-fill);border:0;border-radius:var(--radius-sm);padding:calc(var(--space-1) + 1px) calc(var(--space-2) + 1px);margin-right:var(--space-2);cursor:pointer}@keyframes goal-preview-in{from{opacity:0;transform:translateX(calc(0px - var(--space-1)))}to{opacity:1;transform:none}}@media(prefers-reduced-motion:reduce){.goal-preview{animation:none}}
 .preview-checklist{padding:var(--space-3) var(--space-4) var(--space-4)}
 .preview-checklist :global([data-block="checklist"]){border:0;border-radius:0;background:transparent}
 .preview-checklist :global([data-block="checklist"] .checklist-header){margin:0 calc(var(--page-row-outset) * -1) var(--space-1)}
 .preview-checklist :global([data-block="checklist"] .checklist-header .header){padding:6px var(--page-row-outset)}
 .preview-checklist :global([data-block="checklist"] .body-inner){padding:0}
 .preview-checklist :global([data-block="checklist"] .step-inner){padding:0}
 .preview-checklist :global([data-block="checklist"] .group-toggle){padding:0 0 0 calc(var(--page-check-target) + var(--space-3));margin-inline:0}
</style>
