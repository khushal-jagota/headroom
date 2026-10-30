<script lang="ts">
 import {setContext,untrack} from 'svelte';
 import {createQuery} from '@tanstack/svelte-query';
 import {queries} from '../../lib/queryCatalogue';
 import {goalQueries} from '../../lib/goals/queries';
 import {goalDateText,refreshAtBoundary} from '../../lib/goals/presentation';
 import {selectedChecklist} from '../../lib/goals/checklistCompletion';
 import {createGoalEditor} from '../../lib/goals/editing';
 import type {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {goalPreviewPosition,GOAL_PREVIEW_WIDTH,GOAL_PREVIEW_GUTTER} from '../../lib/goals/railPreview';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
 import type {ChartBlock} from '../../lib/goals/contracts';
 import PageChart from '../pageKit/PageChart.svelte';
 import PageChecklist from '../pageKit/PageChecklist.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {id,target,rail,instant=false,sessions,onKeepOpen=()=>undefined,onLeave=()=>undefined,element=$bindable<HTMLElement|null>(null)}:{
  id:string;target:HTMLElement|null;rail:HTMLElement|null;instant?:boolean;sessions:ReturnType<typeof createGoalRequestSessions>;
  onKeepOpen?:()=>void;onLeave?:(target:HTMLElement)=>void;element?:HTMLElement|null;
 }=$props();
 // The parent keys this component by goal identity. Writes keep this captured ID.
 const goalId=untrack(()=>id);
 const anchor=untrack(()=>target);
 const editor=createGoalEditor(goalId);
 const binarySession=untrack(()=>sessions.binary(goalId));
 let receiptVersion=$state(0);
 $effect(()=>binarySession.subscribe(()=>receiptVersion++));
 let binaryReceipt=$derived.by(()=>{receiptVersion;return binarySession.current;});
 const detail=createQuery(()=>goalQueries.detail(goalId)),readings=createQuery(()=>goalQueries.readings(goalId));
 const projects=createQuery(()=>queries.projects());
 let goal=$derived(detail.data);
 let project=$derived(projects.data?.projects.find(project=>project.id===goal?.project_id));
 let checklist=$derived(goal?.kind==='binary'?selectedChecklist(goal.page,goal.primary_checklist_block_id):null);
 let chart=$derived<ChartBlock|null>(checklist?null:goal?.kind==='binary'?{type:'chart',id:'completion',kind:'binary',placement:'primary'}:goal?.primary_chart??null);
 setContext<PageContext>(PAGE_CONTEXT,{
  get goalId(){return goalId;},get revision(){return goal?.revision??0;},get narrow(){return true;},
  get values(){return readings.data?.values??[];},get charts(){return readings.data?.charts;},
  get completed(){return goal?.completed??false;},get status(){return goal?.status??'neutral';},
  updateBlock:editor.updateBlock,
  checklistSession:(blockId,itemId)=>sessions.checklist(goalId,blockId,itemId),
  manualSession:(measureId)=>sessions.manual(goalId,measureId)
 });
 $effect(()=>{if(goal)return refreshAtBoundary(Math.min(goal.next_refresh_at,readings.data?.next_refresh_at??Infinity),()=>{void detail.refetch();void readings.refetch();});});
 async function complete(completed:boolean){await binarySession.run({completed},()=>editor.write({completed})).catch(()=>undefined);}
 let left=$state(0),top=$state(0);
 function containClicks(node:HTMLElement){
  const stop=(event:MouseEvent)=>event.stopPropagation();
  node.addEventListener('click',stop);
  return{destroy(){node.removeEventListener('click',stop);}};
 }
 $effect(()=>{
  if(!element||!target||!rail)return;
  const panel=element,anchor=target,currentRail=rail;
  const place=()=>{
   const position=goalPreviewPosition(anchor.getBoundingClientRect(),currentRail.getBoundingClientRect().right,panel.offsetWidth,panel.offsetHeight,window.innerWidth,window.innerHeight);
   left=position.left;top=position.top;
  };
  const observer=new ResizeObserver(place);
  observer.observe(panel);observer.observe(anchor);observer.observe(currentRail);place();
  window.addEventListener('resize',place);window.addEventListener('scroll',place,true);
  return()=>{observer.disconnect();window.removeEventListener('resize',place);window.removeEventListener('scroll',place,true);};
 });
</script>
<!-- The panel follows its row in DOM order, so native Tab reaches its controls. -->
<section use:containClicks bind:this={element} data-goal-preview={goalId} data-goal-preview-panel id={`goal-preview-${goalId}`} class="goal-preview" class:instant style:left={`${left}px`} style:top={`${top}px`} style:--preview-width={`${GOAL_PREVIEW_WIDTH}px`} style:--preview-gutters={`${GOAL_PREVIEW_GUTTER*2}px`} aria-label={`Goal preview: ${goal?.title??'Goal'}`} onpointerenter={onKeepOpen} onpointerleave={event=>{if(event.pointerType!=='touch'&&anchor)onLeave(anchor);}} onfocusin={onKeepOpen} onfocusout={event=>{if(event.relatedTarget&&!element?.contains(event.relatedTarget as Node)&&event.relatedTarget!==anchor&&anchor)onLeave(anchor);}}>
 {#if goal}
  <header class="preview-header"><div class="preview-date">{#if project}<span class="preview-project">{project.name}</span><span aria-hidden="true">·</span>{/if}<span class:attention={goal.status==='attention'}>{goal.deadline?`By ${goalDateText(goal.deadline)}`:'No deadline'}</span></div><h1>{goal.title}</h1></header>
  {#if checklist}<PageChecklist block={checklist}/>
  {:else if chart}<div class="primary-chart"><PageChart block={chart} compact integrated/>
   {#if goal.kind==='binary'}<div class="panel-actions"><button class="completion-action" disabled={binaryReceipt?.status==='pending'} onclick={()=>complete(!goal!.completed)}>{binaryReceipt?.status==='pending'?'Saving…':goal.completed?'Mark not done':'Mark done'}</button></div>{/if}
  </div>{/if}
  {#if binaryReceipt?.status==='failed'}<div class="preview-error"><ErrorLine error={binaryReceipt.error}/><button onclick={()=>complete(binaryReceipt!.command.completed)}>Retry save</button><button onclick={()=>binarySession.discard()}>Discard</button></div>{/if}
  {#if detail.error}<div class="preview-error"><ErrorLine error={detail.error}/></div>{/if}
  {#if readings.error}<div class="preview-error"><ErrorLine error={readings.error}/></div>{/if}
 {:else if detail.error}<div class="preview-error"><ErrorLine error={detail.error}/></div>{:else}<p class="preview-loading">Loading goal…</p>{/if}
</section>
<style>
 .goal-preview{position:fixed;z-index:40;width:min(var(--preview-width),calc(100vw - var(--preview-gutters)));max-height:calc(100dvh - var(--preview-gutters));overflow:auto;overscroll-behavior:contain;scrollbar-width:thin;border:var(--border-hairline) solid var(--border-color);border-radius:var(--radius-lg);background:var(--surface-1);box-shadow:var(--shadow-float);font-size:var(--type-sm);pointer-events:auto;animation:goal-preview-in var(--motion-base) var(--motion-ease)}
 .goal-preview.instant{animation:none}.preview-header{position:relative;background:var(--surface-2);padding:var(--space-4) var(--space-4) var(--space-5)}.preview-date{display:flex;align-items:center;flex-wrap:wrap;gap:9px;font-size:12px;line-height:1.4;color:var(--text-faint)}.preview-project{color:var(--text-muted)}.attention{color:var(--accent-attention)}h1{margin:var(--space-2) 0 0;font-size:var(--type-lg);line-height:1.35;font-weight:600;letter-spacing:var(--tracking-title);color:var(--text-strong);overflow-wrap:anywhere}.primary-chart{padding:24px 20px}.panel-actions{display:flex;justify-content:flex-end;margin-top:20px}.completion-action{border:1px solid var(--border-color);border-radius:var(--radius-md);background:none;padding:8px 14px;color:var(--text-muted);font:inherit;font-size:13px;cursor:pointer}.completion-action:hover{color:var(--text-strong)}.preview-error,.preview-loading{padding:var(--space-4)}.preview-error button{font:inherit;color:var(--text-muted);background:none;border:var(--border-hairline) solid var(--border-color);border-radius:var(--radius-sm);padding:var(--space-1) var(--space-2);margin-right:var(--space-2);cursor:pointer}@keyframes goal-preview-in{from{opacity:0;transform:translateX(calc(0px - var(--space-1)))}to{opacity:1;transform:none}}@media(prefers-reduced-motion:reduce){.goal-preview{animation:none}}
</style>
