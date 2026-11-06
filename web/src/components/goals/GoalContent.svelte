<script lang="ts">
  import Button from '../Button.svelte';
 import {setContext} from 'svelte';
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {createGoalEditor} from '../../lib/goals/editing';
 import {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {pageWithoutSelectedChecklistForRender} from '../../lib/goals/checklistCompletion';
 import {resolveGoalPrimaryDisplay} from '../../lib/goals/primaryDisplay';
 import {createGoalPageContext} from '../../lib/goals/pageContext';
 import type {GoalEditBody,GoalResponse} from '../../lib/goals/contracts';
 import {PAGE_CONTEXT,PAGE_HOLDER,type PageContext,type PageTextDraft} from '../../lib/pageKit/context';
 import PageDocument from '../pageKit/PageDocument.svelte';
 import PageChart from '../pageKit/PageChart.svelte';
 import PageChecklist from '../pageKit/PageChecklist.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {goal}:{goal:GoalResponse}=$props();
 let id=$derived(goal.id);
 const readings=createQuery(()=>goalQueries.readings(id));
 let width=$state(0),error=$state<unknown>(null);
 let narrow=$derived(width<=640);
 $effect(()=>{if(readings.data)return refreshAtBoundary(readings.data.next_refresh_at,()=>void readings.refetch());});
 setContext(PAGE_HOLDER,()=>goal.holder_kind==='manager'?{kind:'manager',id:goal.holder_id!}:{kind:'chief',id:'chief'});
 const sessions=createGoalRequestSessions();
 let editor=$derived(createGoalEditor(id));
 function save(patch:GoalEditBody){return editor.write(patch);}
 function updateBlock(blockId:string,update:Parameters<typeof editor.updateBlock>[1]){return editor.updateBlock(blockId,update);}
 const textDrafts = new Map<string, PageTextDraft>();
 const authorText: NonNullable<PageContext['authorText']> = {
  draft(blockId, field) {
   const key=JSON.stringify([id,blockId,field]);
   let draft=textDrafts.get(key);
   if(!draft){const created=$state<PageTextDraft>({value:null,error:null,saving:false});draft=created;textDrafts.set(key,created);}
   return draft;
  },
  begin(blockId) {
   const ownerId = goal.id;
   const captured = createGoalEditor(ownerId);
   return update => captured.updateBlock(blockId, update);
  }
 };
 setContext<PageContext>(PAGE_CONTEXT,createGoalPageContext({
  goalId:()=>id,goal:()=>goal,readings:()=>readings.data,narrow:()=>narrow,sessions:()=>sessions,
 },{manualSession:measureId=>sessions.manual(id,measureId),updateBlock,authorText}));
 async function change(patch:GoalEditBody){try{await save(patch);error=null;}catch(e){error=e;}}
 let display=$derived(resolveGoalPrimaryDisplay(goal));
 let checklist=$derived(display.checklist);
 let renderedPage=$derived(pageWithoutSelectedChecklistForRender(goal.page,checklist?.id??null));
 let primary=$derived(display.chart);
</script>
<div class="goal-content" bind:clientWidth={width} data-goal-content={id}>
 {#if error}<ErrorLine {error}/>{/if}
 {#if checklist}<section class="checklist-panel" aria-label="Completion checklist">{#key `${goal.id}:${checklist.id}`}<PageChecklist block={checklist} presentation="normal"/>{/key}</section>{/if}
 {#if primary}<section class="measure-panel" aria-label="Current reading"><PageChart block={primary} integrated/>
  {#if goal.kind==='binary'}<div class="panel-actions"><Button variant="quiet" class="completion-action" onclick={()=>change({completed:!goal.completed})}>{goal.completed?'Mark not done':'Mark done'}</Button></div>{/if}
 </section>{/if}
 {#if readings.error}<ErrorLine error={readings.error}/>{/if}
 {#each readings.data?.sources.filter(s=>s.health!=='healthy')??[] as source(source.source_id)}<p class="source-warning">{source.source_name}: {source.health}</p>{/each}
 <!-- The saved page is the goal's content. It carries its own authored headings; the page
      renderer adds none, and nothing here wraps it in a section of our own naming. -->
 <div class="goal-page">{#key goal.id}<PageDocument document={renderedPage} showHeader={false} supplementary/>{/key}</div>
</div>
<style>
.goal-content{container-type:inline-size}/* The approved card: the chart sits inside it without a second frame. */
.measure-panel{margin-top:0;padding:var(--page-card-pad);border:var(--border-hairline) solid var(--page-card-edge);border-radius:var(--page-card-radius);background:var(--page-card-fill)}.measure-panel :global(.completion-card){min-height:124px}.panel-actions{display:flex;justify-content:flex-end;margin-top:var(--space-4)}.goal-page{margin-top:var(--page-section-gap)}.source-warning{font-size:var(--type-small);color:var(--accent-attention,#d4ab6c)}@container (max-width:640px){.measure-panel{padding:24px 20px}}
</style>
