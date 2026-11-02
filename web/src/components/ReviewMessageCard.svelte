<script lang="ts">
  import { onMount, tick, untrack, type Snippet } from 'svelte';
  import { createQuery } from '@tanstack/svelte-query';
  import type { ReviewMessageItem } from '../lib/types';
  import { queries } from '../lib/queryCatalogue';
  import { selectedRecap } from '../lib/navigation/views/recap.svelte';
  import { navigationApplication } from '../lib/navigation/application.svelte';
  import { PlannerFetchError } from '../lib/api';
  import { mutateJson } from '../lib/mutate';
  import { conversationOwnerImpact } from '../lib/workspaceNavigationImpact';
  import { ticketCommandImpact } from '../lib/ticketNavigationImpact';
  import { readConversation, readEventsAfter, advanceOwnerRead, messageContentOf, type DeliveredMessage } from '../lib/conversation/wire';
  import { mintOutgoingMessage, recallOutgoingMessages, rememberOutgoingMessages, type OutgoingMessage } from '../lib/conversation/outgoing';
  import { readComposerDraft, writeComposerDraft } from '../lib/conversation/composerDraft';
  import { pendingFilesAsPieces, restoredPendingFiles, type PendingConversationFile } from '../lib/conversation/pendingFiles';
  import { pendingImagesAsPieces, restoredPendingImages, releasePendingImages, type PendingConversationImage } from '../lib/conversation/pendingImages';
  import ReviewReplyInput from './ReviewReplyInput.svelte';
  import { fadeConversationExit } from '../lib/conversation/surfaceMotion';
  import { fateSentence } from '../lib/conversation/composer';
  import { unreadOwnerMessages, reviewMessageHref, reviewReplyEndpoint, reviewReplyReceipt, readReviewDraft, saveReviewDraft, draftStorageKey, attemptStorageKey, type OwnerMessage } from '../lib/review/messages';
  import MessagePieces from './conversation/MessagePieces.svelte';
  import MarkdownBlock from './MarkdownBlock.svelte';
  import ClampedText from './ClampedText.svelte';
  import RequestRecapAction from './RequestRecapAction.svelte';
  import SkeletonRegion from './SkeletonRegion.svelte';
  import ErrorLine from './ErrorLine.svelte';

  let { item, onSkip, onResolved, onRendered, onBusy, footer, embedded=false }: {
    embedded?:boolean; item:ReviewMessageItem; onSkip:()=>void; onResolved:(through:number)=>void;
    onRendered?:()=>void; onBusy?:(busy:boolean)=>void; footer?:Snippet;
  } = $props();
  const navigation = navigationApplication()!;
  // The route keys this component by conversation, never by changing attention facts.
  const conversationId = untrack(()=>item.conversation_id);
  const draftKey = draftStorageKey(conversationId);
  let text = $state(untrack(()=>readComposerDraft(draftKey)));
  let files = $state.raw<PendingConversationFile[]>([]);
  let images = $state.raw<PendingConversationImage[]>([]);
  let attempt = $state.raw<OutgoingMessage|null>(null);
  let retryingUnknown = $state(false);
  let ready = $state(false);
  let loaded = $state(false);
  let events = $state.raw<OwnerMessage[]>([]);
  let error = $state<unknown>(null);
  let busy = $state(false);
  let intakeBusy = $state(false);
  let leaving = $state(false);
  let destroyed = false;
  let card = $state<HTMLDivElement|null>(null);
  let voiceActive = $state(false);
  let nextId = 0;
  const chief = createQuery(()=>({...queries.chiefSettings(),enabled:item.owner_kind==='chief'}));
  const recapBody = selectedRecap(()=>item.recap_reference,()=>true);
  let recap = $derived(item.owner_kind==='chief' ? chief.data?.recap ?? '' : recapBody.body ?? item.recap);
  let disabled = $derived(busy || intakeBusy || !ready || retryingUnknown);
  let hasReply = $derived(Boolean(text.trim() || files.length || images.length));
  let through = $derived(events.at(-1)?.sequence ?? 0);
  $effect(()=>{ onBusy?.(busy || intakeBusy || voiceActive); });
  $effect(()=>{ if(ready) { const content=[...pendingImagesAsPieces(images),...pendingFilesAsPieces(files)]; void saveReviewDraft(conversationId,content).then(saved=>{if(!saved)error=new Error('The attachment could not be saved in this browser.');}); } });

  async function load():Promise<void> {
    error=null;
    try {
      const view=await readConversation(conversationId);
      const rows=await readEventsAfter(conversationId,view.owner_read_through_sequence);
      if(destroyed)return;
      events=unreadOwnerMessages(rows,view.owner_read_through_sequence);
      loaded=true;
      await tick();
      onRendered?.();
      if(events.length===0 && !text && !files.length && !images.length && !attempt) onResolved?.(view.owner_read_through_sequence);
    } catch(problem) { if(!destroyed) error=problem; }
  }
  onMount(()=>{
    void (async()=>{
      const [content,held]=await Promise.all([readReviewDraft(conversationId),recallOutgoingMessages(attemptStorageKey(conversationId))]);
      if(destroyed)return;
      const restoredImages=restoredPendingImages(content,nextId); images=restoredImages.images;nextId=restoredImages.nextId;
      const restoredFiles=restoredPendingFiles(content,nextId);files=restoredFiles.files;nextId=restoredFiles.nextId;
      attempt=held[0]??null; retryingUnknown=attempt!==null;
      ready=true;
      await load();
    })().catch(problem=>{error=problem;});
    return ()=>{destroyed=true;releasePendingImages(images);onBusy?.(false);};
  });

  async function resolve(bound:number):Promise<void> {
    leaving=true;
    // Freeze the painted card through the settled leave beat. Queue refresh can finish
    // independently, but the next item cannot take its slot before this animation ends.
    await Promise.all([navigation.owner.recover().catch(()=>undefined),fadeConversationExit(card)]);
    if(!destroyed)onResolved(bound);
  }
  async function markRead():Promise<void> {
    if(busy || !loaded || !through || voiceActive)return;
    busy=true;error=null;
    try { const bound=through;await advanceOwnerRead(conversationId,bound);await resolve(bound); }
    catch(problem){error=problem;}finally{busy=false;}
  }
  async function clearAttempt() {attempt=null;retryingUnknown=false;await rememberOutgoingMessages(attemptStorageKey(conversationId),[]);}
  async function accepted(bound:number) {
    // Keep the field and attachment geometry painted until the leave finishes.
    writeComposerDraft(draftKey,'');
    await clearAttempt();
    await saveReviewDraft(conversationId,[]);
    await resolve(bound);
  }
  async function send():Promise<void> {
    if(busy || intakeBusy || !ready || !loaded || !hasReply || voiceActive)return;
    busy=true;error=null;
    try {
      if(attempt && retryingUnknown) {
        const receipt=await reviewReplyReceipt(conversationId,attempt.messageId);
        if(receipt?.fate==='prompt'){await accepted(attempt.ownerReadThroughSequence!);return;}
        if(receipt?.fate==='prompt_uncertain') { error=new Error('The delivery is uncertain. Your reply is kept here.'); return; }
        if(receipt) {
          await clearAttempt();
          error=new Error('The reply did not reach the agent. Your reply is kept here.');
          return;
        }
      }
      if(attempt && retryingUnknown) {
        const current=await readConversation(conversationId);
        if(current.held_prompts.some(prompt=>prompt.sender_message_id===attempt?.messageId)) {await accepted(attempt.ownerReadThroughSequence!);return;}
      }
      if(!attempt) {
        attempt={...mintOutgoingMessage({content:[...(text.trim()?[{piece:'text' as const,text:text.trim()}]:[]),...pendingImagesAsPieces(images),...pendingFilesAsPieces(files)],senderLabel:'owner',mode:'queue'}),ownerReadThroughSequence:through};
        if(!await rememberOutgoingMessages(attemptStorageKey(conversationId),[attempt])) {attempt=null;throw new Error('The reply could not be saved in this browser.');}
      }
      const sending=attempt;
      const effective=navigation.state.effective;
      const impact=item.owner_kind==='ticket' ? ticketCommandImpact(effective,item.owner_id,'conversation') : conversationOwnerImpact(effective?navigation.presentationFor(effective):undefined,item.owner_kind,item.owner_id);
      retryingUnknown=true;
      const result=await navigation.runCommand(impact,responseMetadata=>mutateJson<DeliveredMessage>(reviewReplyEndpoint(item),{method:'POST',responseMetadata,body:{
        conversation_id:conversationId,content:sending.content,sender_label:'owner',mode:'queue',sender_message_id:sending.messageId,
        sent_at_unix_milliseconds:sending.sentAtUnixMilliseconds,owner_read_through_sequence:sending.ownerReadThroughSequence
      }}),'send review reply');
      if(result.fate==='started' || result.fate==='injected' || result.fate==='queued') { await accepted(sending.ownerReadThroughSequence!); }
      else { if(result.fate!=='uncertain')await clearAttempt(); error=new Error(fateSentence(result) || 'The reply did not reach the agent.'); }
    } catch(problem){
      if(problem instanceof PlannerFetchError && problem.status && problem.status>=400 && problem.status<500) await clearAttempt();
      error=problem;
    } finally {busy=false;}
  }
</script>

<div bind:this={card} data-review-card data-review-item-type="message" data-conversation-id={conversationId} data-message-identity={`message:${conversationId}`} data-review-leaving={leaving} data-review-loaded={loaded}>
  {#if !embedded}
  <div class="review-ticket-decision-line">
    <button data-skip disabled={busy || intakeBusy || voiceActive} onclick={onSkip}>Skip &rsaquo;</button>
    <a data-open-conversation href={reviewMessageHref(item)} aria-disabled={busy} onclick={event=>{if(busy)event.preventDefault();}}>Open conversation &rsaquo;</a>
  </div>
  <h1 class="review-ticket-title">{item.title}</h1>
  {#if recap}<ClampedText class="review-recap" contentKey={recap}><MarkdownBlock text={recap}/></ClampedText>{/if}
  {#if item.owner_kind !== "goal"}<div class="review-recap-request"><RequestRecapAction kind={item.owner_kind} id={item.owner_id}/></div>{/if}
  {/if}
  {#if embedded}<div class="review-ticket-decision-line"><a data-open-conversation href={reviewMessageHref(item)}>Open conversation &rsaquo;</a></div>{/if}
  <SkeletonRegion shape="block" loading={!loaded && !error} loadingText="Loading messages...">
    {#if loaded}
      <div class="review-message-card">
        <div class="review-message-run">
          {#each events as event (event.sequence)}<div class="review-message-said" data-review-message-sequence={event.sequence}><MessagePieces content={messageContentOf(event.payload)} {conversationId}/></div>{/each}
        </div>
        <ReviewReplyInput bind:text bind:files bind:images bind:intakeBusy bind:voiceActive {draftKey}
          {disabled} sendDisabled={busy || !ready} sending={busy}
          sendLabel={retryingUnknown?'Retry reply':'Send reply'} onSend={send} onError={problem=>error=problem}/>
        <div class="review-read-row"><button data-review-mark-read disabled={busy || intakeBusy || !through || voiceActive} onclick={()=>void markRead()}>Mark read</button></div>
        {#if error}<ErrorLine {error}/>{/if}
      </div>
    {:else if error}<ErrorLine {error}/><button class="button button--quiet" onclick={()=>void load()}>Retry</button>{/if}
  </SkeletonRegion>
</div>
{@render footer?.()}

<style>
  .review-message-card{margin-top:var(--space-5);padding:var(--space-5);background:var(--surface-2);border-radius:var(--radius-lg)}
  .review-message-run{font-family:var(--font-ui);font-size:var(--type-md);line-height:1.68}
  .review-message-said+.review-message-said{position:relative;margin-top:var(--space-5);padding-top:var(--space-4)}
  .review-message-said+.review-message-said::before{content:"";position:absolute;inset-block-start:0;inset-inline-start:0;inline-size:var(--space-6);border-block-start:var(--border-hairline) solid var(--text-faintest);opacity:.55}
  .review-recap-request{margin-top:var(--space-2);font-size:var(--type-sm)}
  button:disabled{opacity:.5;cursor:default}
  .review-read-row button:hover:not(:disabled){background-image:var(--interaction-hover);color:var(--text-strong)}
  .review-read-row{margin-top:var(--space-3);margin-left:calc(var(--space-3) * -1)}
  .review-read-row button{font:inherit;font-size:var(--type-sm);line-height:1.6;padding:8px 12px;border:0;border-radius:var(--radius-pill);background:none;color:var(--text-faint);cursor:pointer}
</style>
