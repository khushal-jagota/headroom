<script lang="ts">
 import {setContext,onDestroy} from 'svelte';
 import {createQuery} from '@tanstack/svelte-query';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 import {goalQueries} from '../../lib/goals/queries';
 import {refreshAtBoundary,updatePageBlock} from '../../lib/goals/presentation';
 import type {GoalResponse,Block,ChartBlock,MeasureValue} from '../../lib/goals/contracts';
 import {PAGE_CONTEXT,type PageContext} from '../../lib/pageKit/context';
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
 import GoalHeader from './GoalHeader.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {id,requestedConversationId=null,openedFile=null,onOpenFile}:{id:string;requestedConversationId?:string|null;openedFile?:ManagedFileTarget|null;onOpenFile?:(file:ManagedFileTarget|null)=>void}=$props();
 const navigation=navigationApplication();
 const detail=createQuery(()=>goalQueries.detail(id)),readings=createQuery(()=>goalQueries.readings(id)),start=createQuery(()=>goalQueries.start(id));
 let goal=$derived(detail.data),narrow=$state(false),content=$state<HTMLElement|null>(null),error=$state<unknown>(null);
 let conversationState=$state<ConversationState>('rest'),selectedPastConversationId=$state<string|null>(null),backends=$state<readonly BackendSnapshot[]>([]);
 let conversationId=$derived(selectedPastConversationId??goal?.conversation_id??null);
 let requestedApplied:string|null|undefined;
 $effect(()=>{if(goal&&requestedApplied!==requestedConversationId){requestedApplied=requestedConversationId;selectedPastConversationId=requestedConversationId&&requestedConversationId!==goal.conversation_id?requestedConversationId:null;if(requestedConversationId)conversationState='opened';}});
 $effect(()=>{navigation?.owner.setDemand('goal',{selected:{kind:'goal',id},selected_conversation_id:selectedPastConversationId,windows:[]});return()=>navigation?.owner.setDemand('goal',undefined);});
 $effect(()=>{if(content){const observer=new ResizeObserver(([entry])=>narrow=entry.contentRect.width<=640);observer.observe(content);return()=>observer.disconnect();}});
 $effect(()=>{if(goal)return refreshAtBoundary(Math.min(goal.next_refresh_at,readings.data?.next_refresh_at??Infinity),()=>{void detail.refetch();void readings.refetch();});});
 $effect(()=>{void readBackends().then(value=>backends=value).catch(()=>undefined);});
 async function save(patch:Partial<GoalResponse>,revision?:number){
  if(!goal)throw new Error('Goal is not loaded');
  const accepted=await mutateJson<GoalResponse>(`/api/goals/${encodeURIComponent(id)}`,{method:'PATCH',body:{expected_revision:revision??goal.revision,...patch}});
  await detail.refetch();return accepted;
 }
 let editQueue:Promise<unknown>=Promise.resolve();
 function updateBlock(blockId:string,update:(block:Block)=>Block,revision?:number):Promise<unknown>{
  const operation=editQueue.catch(()=>undefined).then(()=>{if(!goal)throw new Error('Goal is not loaded');return save({page:{...goal.page,blocks:updatePageBlock(goal.page.blocks,blockId,update)}},revision);});editQueue=operation;return operation;
 }
 setContext<PageContext>(PAGE_CONTEXT,{get goalId(){return id},get revision(){return goal?.revision??0},get narrow(){return narrow},get values(){return readings.data?.values??[]},get completed(){return goal?.completed??false},get status(){return goal?.status??'neutral'},updateBlock});
 async function change(patch:Partial<GoalResponse>){try{await save(patch);error=null;}catch(e){error=e;}}
 async function send(body:OwnerSendBody):Promise<DeliveredMessage>{const operation=(responseMetadata?: import('../../lib/api').FetchOptions['responseMetadata'])=>mutateJson<DeliveredMessage>(`/api/goals/${encodeURIComponent(id)}/conversation/send`,{method:'POST',body,responseMetadata});const view=navigation?.state.effective;const delivered=navigation?await navigation.runCommand(conversationOwnerImpact(view?navigation.presentationFor(view):undefined,'goal',id),operation,'send goal message'):await operation();await detail.refetch();return delivered;}
 async function reset(){const operation=(responseMetadata?: import('../../lib/api').FetchOptions['responseMetadata'])=>mutateJson(`/api/goals/${encodeURIComponent(id)}/conversation/reset`,{method:'POST',responseMetadata});const view=navigation?.state.effective;if(navigation)await navigation.runCommand(conversationOwnerImpact(view?navigation.presentationFor(view):undefined,'goal',id),operation,'reset goal conversation');else await operation();selectedPastConversationId=null;await detail.refetch();}
 let primary=$derived<ChartBlock|null>(goal?.kind==='binary'?{type:'chart',id:'completion',kind:'binary',placement:'primary'}:goal?.primary_chart??null);
</script>
<ManagerFrame class="goal-workspace" bind:conversationState {openedFile} {onOpenFile}>
 <div class="goal-content" bind:this={content} data-goal-page={id}>
 <nav class="breadcrumb" aria-label="Breadcrumb"><a href={workspaceAddress({kind:'chief'})}>Chief of Staff</a><span>/</span><a href={workspaceAddress({kind:'goals'})}>Goals</a></nav>
 {#if goal}
 <GoalHeader {goal} onSave={save}/>
 <div class="goal-actions"><ConversationSignalMark signal={navigationConversationSignal(navigation?.selectors.effective,goal.conversation_id)} conversationId={goal.conversation_id}/><button class="quiet" onclick={()=>change({active:!goal!.active})}>{goal.active?'Make inactive':'Activate'}</button></div>
 {#if error}<ErrorLine {error}/>{/if}
 {#if primary}<div class="primary-chart"><PageChart block={primary}/></div>{/if}
 {#if goal.kind==='binary'}<button class="completion-action" onclick={()=>change({completed:!goal!.completed})}>{goal.completed?'Mark not done':'Mark done'}</button>{/if}
 {#if readings.error}<ErrorLine error={readings.error}/>{/if}
 {#each readings.data?.sources.filter(s=>s.health!=='healthy')??[] as source(source.source_id)}<p class="source-warning">{source.source_name}: {source.health}</p>{/each}
 <section class="approach" aria-label="Approach"><h2>Approach</h2><PageDocument document={goal.page} showHeader={false}/></section>
 {:else if detail.error}<ErrorLine error={detail.error}/>{:else}<p>Loading goal…</p>{/if}
 </div>
 {#snippet conversation()}<LiveConversation ownerLoading={!goal} bind:conversationState {conversationId} bind:selectedPastConversationId readOnly={selectedPastConversationId!==null} persistenceKey={`owner:goal:${id}`} label={goal?.title??'Goal'} bind:backends startValues={start.data??null} senderLabel="owner" sendMessage={send} onNewConversation={reset} historyOwner={{kind:'goal',id}}/>{/snippet}
</ManagerFrame>
<style>
.goal-content{max-width:900px;margin:0 auto;padding-top:32px}.breadcrumb{display:flex;gap:12px;align-items:center;color:var(--text-faintest);font-size:13px;margin-bottom:32px}.breadcrumb a{color:inherit;text-decoration:none}.goal-actions{display:flex;gap:12px;justify-content:flex-end;margin-bottom:12px}.quiet,.completion-action{border:0;background:none;padding:0;color:var(--text-muted);font:inherit;font-size:12px;cursor:pointer}.completion-action{color:var(--accent-bright);font-size:13px;margin-top:12px}.approach{border-top:1px solid var(--border-color);margin-top:28px;padding-top:24px}h2{font-size:14px;font-weight:500;margin:0 0 16px;color:var(--text-muted)}.source-warning{font-size:12px;color:var(--accent-attention,#d4ab6c)}
</style>
