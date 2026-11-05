<script lang="ts">
 import GoalContent from './GoalContent.svelte';
 import {workspaceAddress} from '../../lib/workspaceAddress';
 import {createQuery} from '@tanstack/svelte-query';
 import {queries} from '../../lib/queryCatalogue';
 import {refreshAtBoundary} from '../../lib/goals/presentation';
 import {goalQueries} from '../../lib/goals/queries';
 import {navigationApplication} from '../../lib/navigation/application.svelte';
 import {mutateJson} from '../../lib/mutate';
 import {fetchJson} from '../../lib/api';
 import {queryClient} from '../../lib/queryClient';
 import {readBackends,type BackendSnapshot,type DeliveredMessage,type OwnerSendBody} from '../../lib/conversation/wire';
 import type {ConversationState} from '../../lib/conversation/conversationState';
 import type {ManagedFileTarget} from '../../lib/filePreview';
 import ManagerFrame from '../ManagerFrame.svelte';
 import LiveConversation from '../conversation/LiveConversation.svelte';
 import GoalHeader from './GoalHeader.svelte';
 import ErrorLine from '../ErrorLine.svelte';
 let {id,requestedConversationId=null,openedFile=null,onOpenFile}:{id:string;requestedConversationId?:string|null;openedFile?:ManagedFileTarget|null;onOpenFile?:(file:ManagedFileTarget|null)=>void}=$props();
 const navigation=navigationApplication();
 const detail=createQuery(()=>goalQueries.detail(id));
 let goal=$derived(detail.data),error=$state<unknown>(null);
 $effect(()=>{if(goal)return refreshAtBoundary(goal.next_refresh_at,()=>void detail.refetch());});
 let holder=$derived(goal?.holder_kind==='manager'?{kind:'manager' as const,id:goal.holder_id!}:{kind:'chief' as const,id:'chief'});
 let shared=$derived(navigation?.selectors.effective?navigation.presentationFor(navigation.selectors.effective):null);
 let holderConversationId=$derived(holder.kind==='chief'?shared?.chief?.conversation_id:shared?.managersById.get(holder.id)?.conversation_id);
 const start=createQuery(()=>({...(holder.kind==='chief'?queries.chiefConversationStartValues():queries.managerConversationStartValues(holder.id)),enabled:goal!==undefined}));
 let conversationPath=$derived(holder.kind==='chief'?'/api/chief/conversation':`/api/managers/${encodeURIComponent(holder.id)}/conversation`);
 let legacyBrowsing=$state(false);
 let conversationState=$state<ConversationState>('rest'),selectedPastConversationId=$state<string|null>(null),backends=$state<readonly BackendSnapshot[]>([]);
 let currentConversationId=$state<string|null>(null);
 let observedPointer=$state<string|undefined>();
 $effect(()=>{const pointer=JSON.stringify([holder.kind,holder.id,holderConversationId]);if(holderConversationId!==undefined&&pointer!==observedPointer){observedPointer=pointer;currentConversationId=holderConversationId;}});
 let conversationId=$derived(selectedPastConversationId??currentConversationId);
 let requestedApplied:string|null|undefined;
 $effect(()=>{if(goal&&holderConversationId!==undefined&&requestedApplied!==requestedConversationId){requestedApplied=requestedConversationId;legacyBrowsing=requestedConversationId===goal.legacy_conversation_id;selectedPastConversationId=requestedConversationId&&requestedConversationId!==holderConversationId?requestedConversationId:null;if(requestedConversationId&&!openedFile)conversationState='opened';}});
 $effect(()=>{if(goal)navigation?.owner.setDemand('goal',{selected:legacyBrowsing&&selectedPastConversationId?{kind:'goal',id}:holder,selected_conversation_id:selectedPastConversationId,windows:[]});return()=>navigation?.owner.setDemand('goal',undefined);});
 $effect(()=>{void readBackends().then(value=>backends=value).catch(()=>undefined);});
 async function send(body:OwnerSendBody):Promise<DeliveredMessage>{const delivered=await mutateJson<DeliveredMessage>(`${conversationPath}/send`,{method:'POST',body});currentConversationId=delivered.conversation_id;return delivered;}
 async function reset(){await mutateJson(`${conversationPath}/reset`,{method:'POST'});selectedPastConversationId=null;currentConversationId=null;}
 let promoting=$state(false);
 async function makeManager(){
  if(!goal||promoting)return;
  promoting=true;
  try{const result=await fetchJson<{manager_id:string}>(`/api/goals/${encodeURIComponent(id)}/make-manager`,{method:'POST',body:{}});window.location.hash=workspaceAddress({kind:'manager',id:result.manager_id});void queryClient.invalidateQueries();}catch(failure){error=failure;}finally{promoting=false;}
 }
 let holderLabel=$derived(holder.kind==='chief'?'Chief of Staff':navigation?.selectors.effective?navigation.presentationFor(navigation.selectors.effective).managersById.get(holder.id)?.title??'Manager':'Manager');
</script>
<ManagerFrame class="goal-workspace" bind:conversationState {openedFile} {onOpenFile}>
 <div class="goal-content" data-goal-page={id}>
 {#if goal}
 <GoalHeader {goal}>{#snippet mark()}{#if goal.holder_kind==='chief'}<button class="make-manager" disabled={promoting} aria-busy={promoting} onclick={makeManager}>{promoting?'Making Manager…':'Make it a Manager'}</button>{/if}{/snippet}</GoalHeader>
 {#if error}<ErrorLine {error}/>{/if}
 <GoalContent {goal}/>
 {#if goal.legacy_conversation_id}<button class="previous-conversation" onclick={()=>{legacyBrowsing=true;selectedPastConversationId=goal!.legacy_conversation_id;conversationState='opened';}}>Previous goal conversation</button>{/if}
 {:else if detail.error}<ErrorLine error={detail.error}/>{:else}<p>Loading goal…</p>{/if}
 </div>
 {#snippet conversation()}<LiveConversation ownerLoading={!goal} bind:conversationState {conversationId} bind:selectedPastConversationId readOnly={selectedPastConversationId!==null} persistenceKey={`owner:${holder.kind}:${holder.id}`} label={holderLabel} composerPlaceholder={holder.kind==='manager'?'Message this manager…':'Message the Chief…'} bind:backends startValues={start.data??null} senderLabel="owner" sendMessage={send} onNewConversation={reset} historyOwner={legacyBrowsing&&selectedPastConversationId?{kind:'goal',id}:holder.kind==='manager'?holder:undefined} uploadOwner={holder}/>{/snippet}
</ManagerFrame>
<style>
.goal-content{container-type:inline-size;max-width:900px;margin:0 auto;padding-top:32px}.make-manager,.previous-conversation{border:0;background:none;color:var(--text-muted);font:inherit;font-size:12px;padding:0;cursor:pointer}.make-manager:hover,.previous-conversation:hover{color:var(--text-strong)}.previous-conversation{margin-top:24px}@media(max-width:760px){.goal-content{padding-top:24px}}
</style>
