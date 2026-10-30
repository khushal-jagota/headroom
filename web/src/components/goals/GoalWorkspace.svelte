<script lang="ts">
 import {setContext} from 'svelte';
 import {createQuery} from '@tanstack/svelte-query';
 import {goalQueries} from '../../lib/goals/queries';
 import {createGoalEditor} from '../../lib/goals/editing';
 import {createGoalRequestSessions} from '../../lib/goals/requestSessions';
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {selectedChecklist,pageWithoutSelectedChecklistForRender} from '../../lib/goals/checklistCompletion';
 import type {GoalResponse,GoalEditBody,ChartBlock} from '../../lib/goals/contracts';
 import {PAGE_CONTEXT,type PageContext,type PageTextDraft} from '../../lib/pageKit/context';
 import {queryClient} from '../../lib/queryClient';
 import ConversationSignalMark from '../ConversationSignalMark.svelte';
 import {navigationConversationSignal} from '../../lib/navigation/presentation';
 import {conversationOwnerImpact} from '../../lib/workspaceNavigationImpact';
 import {navigationApplication} from '../../lib/navigation/application.svelte';
 import {mutateJson} from '../../lib/mutate';
 import {readBackends,type BackendSnapshot,type DeliveredMessage,type OwnerSendBody} from '../../lib/conversation/wire';
 import type {ConversationState} from '../../lib/conversation/conversationState';
 import type {ManagedFileTarget} from '../../lib/filePreview';
 import ManagerFrame from '../ManagerFrame.svelte';
 import LiveConversation from '../conversation/LiveConversation.svelte';
 import PageDocument from '../pageKit/PageDocument.svelte';
 import PageChart from '../pageKit/PageChart.svelte';
 import PageChecklist from '../pageKit/PageChecklist.svelte';
 import GoalHeader from './GoalHeader.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {id,requestedConversationId=null,openedFile=null,onOpenFile}:{id:string;requestedConversationId?:string|null;openedFile?:ManagedFileTarget|null;onOpenFile?:(file:ManagedFileTarget|null)=>void}=$props();
 const navigation=navigationApplication();
 const detail=createQuery(()=>goalQueries.detail(id)),readings=createQuery(()=>goalQueries.readings(id)),start=createQuery(()=>goalQueries.start(id));
 let goal=$derived(detail.data),narrow=$state(false),content=$state<HTMLElement|null>(null),error=$state<unknown>(null);
 let conversationState=$state<ConversationState>('rest'),selectedPastConversationId=$state<string|null>(null),backends=$state<readonly BackendSnapshot[]>([]);
 let conversationId=$derived(selectedPastConversationId??goal?.conversation_id??null);
 let requestedApplied:string|null|undefined;
 $effect(()=>{if(goal&&requestedApplied!==requestedConversationId){requestedApplied=requestedConversationId;selectedPastConversationId=requestedConversationId&&requestedConversationId!==goal.conversation_id?requestedConversationId:null;if(requestedConversationId&&!openedFile)conversationState='opened';}});
 $effect(()=>{navigation?.owner.setDemand('goal',{selected:{kind:'goal',id},selected_conversation_id:selectedPastConversationId,windows:[]});return()=>navigation?.owner.setDemand('goal',undefined);});
 $effect(()=>{if(content){const observer=new ResizeObserver(([entry])=>narrow=entry.contentRect.width<=640);observer.observe(content);return()=>observer.disconnect();}});
 $effect(()=>{if(goal)return refreshAtBoundary(Math.min(goal.next_refresh_at,readings.data?.next_refresh_at??Infinity),()=>{void detail.refetch();void readings.refetch();});});
 $effect(()=>{void readBackends().then(value=>backends=value).catch(()=>undefined);});
 const sessions=createGoalRequestSessions();
 let editor=$derived(createGoalEditor(id));
 function save(patch:Omit<GoalEditBody,'expected_revision'>,revision?:number){return editor.write(patch,revision);}
 function updateBlock(blockId:string,update:Parameters<typeof editor.updateBlock>[1],revision?:number){return editor.updateBlock(blockId,update,revision);}
 const textDrafts = new Map<string, PageTextDraft>();
 const authorText: NonNullable<PageContext['authorText']> = {
  draft(blockId, field) {
   const key = JSON.stringify([id, blockId, field]);
   let draft = textDrafts.get(key);
   if (!draft) {
    const created = $state<PageTextDraft>({value:null,error:null,saving:false});
    draft = created;
    textDrafts.set(key, created);
   }
   return draft;
  },
  begin(blockId) {
   if (!goal || goal.id !== id) throw new Error('The goal is not ready for this edit.');
   const ownerId = goal.id, revision = goal.revision;
   const captured = createGoalEditor(ownerId);
   return async update => {
    try { return await captured.updateBlock(blockId, update, revision); }
    catch (failure) { void queryClient.invalidateQueries(); throw failure; }
   };
  }
 };
 setContext<PageContext>(PAGE_CONTEXT,{get goalId(){return id},get revision(){return goal?.revision??0},get narrow(){return narrow},get values(){return readings.data?.values??[]},get charts(){return readings.data?.charts},get completed(){return goal?.completed??false},get status(){return goal?.status??'neutral'},get statusDate(){return goal?.status_date},checklistDeadlineSession:(blockId,itemId)=>sessions.checklistDeadline(id,blockId,itemId),checklistSession:(blockId,itemId)=>sessions.checklist(id,blockId,itemId),manualSession:measureId=>sessions.manual(id,measureId),updateBlock,authorText});
 async function change(patch:Omit<GoalEditBody,'expected_revision'>){try{await save(patch);error=null;}catch(e){error=e;}}
 async function send(body:OwnerSendBody):Promise<DeliveredMessage>{const operation=(responseMetadata?: import('../../lib/api').FetchOptions['responseMetadata'])=>mutateJson<DeliveredMessage>(`/api/goals/${encodeURIComponent(id)}/conversation/send`,{method:'POST',body,responseMetadata});const view=navigation?.state.effective;const delivered=navigation?await navigation.runCommand(conversationOwnerImpact(view?navigation.presentationFor(view):undefined,'goal',id),operation,'send goal message'):await operation();await detail.refetch();return delivered;}
 async function reset(){const operation=(responseMetadata?: import('../../lib/api').FetchOptions['responseMetadata'])=>mutateJson(`/api/goals/${encodeURIComponent(id)}/conversation/reset`,{method:'POST',responseMetadata});const view=navigation?.state.effective;if(navigation)await navigation.runCommand(conversationOwnerImpact(view?navigation.presentationFor(view):undefined,'goal',id),operation,'reset goal conversation');else await operation();selectedPastConversationId=null;await detail.refetch();}
 let checklist=$derived(goal?.kind==='binary'?selectedChecklist(goal.page,goal.primary_checklist_block_id):null);
 let renderedPage=$derived(goal?pageWithoutSelectedChecklistForRender(goal.page,checklist?.id??null):null);
 let primary=$derived<ChartBlock|null>(checklist?null:goal?.kind==='binary'?{type:'chart',id:'completion',kind:'binary',placement:'primary'}:goal?.primary_chart??null);
</script>
<ManagerFrame class="goal-workspace" bind:conversationState {openedFile} {onOpenFile}>
 <div class="goal-content" bind:this={content} data-goal-page={id}>
 {#if goal}
 <GoalHeader {goal}>{#snippet mark()}<ConversationSignalMark signal={navigationConversationSignal(navigation?.selectors.effective,goal.conversation_id)} conversationId={goal.conversation_id}/>{/snippet}</GoalHeader>
 {#if error}<ErrorLine {error}/>{/if}
 {#if checklist}<section class="checklist-panel" aria-label="Completion checklist">{#key `${goal.id}:${checklist.id}`}<PageChecklist block={checklist} presentation="normal"/>{/key}</section>{/if}
 {#if primary}<section class="measure-panel" aria-label="Current reading"><PageChart block={primary} integrated/>
  {#if goal.kind==='binary'}<div class="panel-actions"><button class="completion-action" onclick={()=>change({completed:!goal!.completed})}>{goal.completed?'Mark not done':'Mark done'}</button></div>{/if}
 </section>{/if}
 {#if readings.error}<ErrorLine error={readings.error}/>{/if}
 {#each readings.data?.sources.filter(s=>s.health!=='healthy')??[] as source(source.source_id)}<p class="source-warning">{source.source_name}: {source.health}</p>{/each}
 <!-- The saved page is the goal's content. It carries its own authored headings; the page
      renderer adds none, and nothing here wraps it in a section of our own naming. -->
 {#if renderedPage}<div class="goal-page">{#key goal.id}<PageDocument document={renderedPage} showHeader={false} supplementary/>{/key}</div>{/if}
 {:else if detail.error}<ErrorLine error={detail.error}/>{:else}<p>Loading goal…</p>{/if}
 </div>
 {#snippet conversation()}<LiveConversation ownerLoading={!goal} bind:conversationState {conversationId} bind:selectedPastConversationId readOnly={selectedPastConversationId!==null} persistenceKey={`owner:goal:${id}`} label={goal?.title??'Goal'} bind:backends startValues={start.data??null} senderLabel="owner" sendMessage={send} onNewConversation={reset} historyOwner={{kind:'goal',id}}/>{/snippet}
</ManagerFrame>
<style>
.goal-content{container-type:inline-size;max-width:900px;margin:0 auto;padding-top:32px}/* The approved card: the chart sits inside it without a second frame. */
.measure-panel{margin-top:28px;padding:28px;border:1px solid var(--chart-card-border);border-radius:28px;background:var(--chart-surface)}.measure-panel :global(.completion-card){min-height:124px}.panel-actions{display:flex;justify-content:flex-end;margin-top:20px}.completion-action{border:1px solid var(--border-color);border-radius:var(--radius-md);background:none;padding:8px 14px;color:var(--text-muted);font:inherit;font-size:13px;cursor:pointer}.completion-action:hover{color:var(--text-strong)}.goal-page{margin-top:40px}.source-warning{font-size:12px;color:var(--accent-attention,#d4ab6c)}@container (max-width:640px){.measure-panel{padding:24px 20px}}@media (max-width:760px){.goal-content{padding-top:24px}}
</style>
