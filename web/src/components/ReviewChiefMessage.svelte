<script lang="ts">
  import {onMount,untrack} from 'svelte';
  import ReviewReplyInput from './ReviewReplyInput.svelte';
  import {PlannerFetchError} from '../lib/api';
  import {mutateJson} from '../lib/mutate';
  import {navigationApplication} from '../lib/navigation/application.svelte';
  import {conversationOwnerImpact} from '../lib/workspaceNavigationImpact';
  import {workspaceAddress} from '../lib/workspaceAddress';
  import {readComposerDraft,writeComposerDraft} from '../lib/conversation/composerDraft';
  import {mintOutgoingMessage} from '../lib/conversation/outgoing';
  import {pendingImagesAsPieces,restoredPendingImages,releasePendingImages,type PendingConversationImage} from '../lib/conversation/pendingImages';
  import {pendingFilesAsPieces,restoredPendingFiles,type PendingConversationFile} from '../lib/conversation/pendingFiles';
  import {sendBodyFor,fateSentence} from '../lib/conversation/composer';
  import {readConversation,messageContentText,type DeliveredMessage} from '../lib/conversation/wire';
  import {readReviewDraft,saveReviewDraft,reviewReplyReceipt} from '../lib/review/messages';
  import {chiefDraftKey,chiefAttachmentDraftId,readChiefPointer,readChiefAttempt,saveChiefAttempt,clearChiefAttempt,type ChiefMessageAttempt} from '../lib/review/chiefMessage';

  let {onBusy}:{onBusy?:(busy:boolean)=>void}=$props();
  const navigation=navigationApplication();
  let text=$state(untrack(()=>readComposerDraft(chiefDraftKey)));
  let files=$state.raw<PendingConversationFile[]>([]);
  let images=$state.raw<PendingConversationImage[]>([]);
  let attempt=$state.raw<ChiefMessageAttempt|null>(null);
  let ready=$state(false);
  let busy=$state(false);
  let intakeBusy=$state(false);
  let voiceActive=$state(false);
  let note=$state('');
  let isError=$state(false);
  let destroyed=false;
  let disabled=$derived(busy || intakeBusy || !ready || attempt!==null);
  $effect(()=>onBusy?.(busy || intakeBusy || voiceActive));
  $effect(()=>{
    if(ready){
      const content=[...pendingImagesAsPieces(images),...pendingFilesAsPieces(files)];
      void saveReviewDraft(chiefAttachmentDraftId,content).then(saved=>{if(!saved)showError(new Error('The attachment could not be saved in this browser.'));}).catch(showError);
    }
  });
  function showError(problem:unknown){if(problem===null){note='';isError=false;return;}note=problem instanceof Error?problem.message:String(problem);isError=true;}
  onMount(()=>{
    void (async()=>{
      const [content,saved]=await Promise.all([readReviewDraft(chiefAttachmentDraftId),readChiefAttempt()]);
      if(destroyed)return;
      const pictures=restoredPendingImages(content,0);images=pictures.images;
      files=restoredPendingFiles(content,pictures.nextId).files;
      attempt=saved;
      if(saved){note='Delivery is not confirmed. Check delivery before you send again.';isError=true;}
      ready=true;
    })().catch(showError);
    return()=>{destroyed=true;onBusy?.(false);};
  });
  async function finish(sending:ChiefMessageAttempt,message:string):Promise<void>{
    await clearChiefAttempt();
    attempt=null;
    // Another Chief surface can replace the shared text while this request waits.
    // Clear only the attempted words, including after a remount checks an old receipt.
    const attemptedText=messageContentText(sending.message.content);
    const storedText=readComposerDraft(chiefDraftKey);
    const storedChanged=storedText.trim()!==attemptedText;
    const localChanged=text.trim()!==attemptedText;
    text=storedChanged?storedText:localChanged?text:'';
    if(!storedChanged)writeComposerDraft(chiefDraftKey,text);
    releasePendingImages(images);images=[];files=[];
    await saveReviewDraft(chiefAttachmentDraftId,[]);
    note=message;isError=false;
  }
  async function keepDraft(message:string):Promise<void>{
    await clearChiefAttempt();attempt=null;note=message;isError=true;
  }
  async function checkDelivery(sending:ChiefMessageAttempt):Promise<void>{
    // A changed Chief pointer never changes the destination captured for this attempt.
    // For a first send only, the current pointer can help locate its correlated receipt.
    const id=sending.expectedConversationId ?? (await readChiefPointer()).conversation_id;
    if(id===null){note='Delivery is not confirmed. Your draft is kept. Open Chief before you send again.';isError=true;return;}
    const receipt=await reviewReplyReceipt(id,sending.message.messageId);
    if(receipt?.fate==='prompt'){await finish(sending,'Sent to Chief');return;}
    if(receipt?.fate==='prompt_uncertain'){note='Delivery is uncertain. Your draft is kept. Do not resend.';isError=true;return;}
    if(receipt){await keepDraft('The message did not reach Chief. Your draft is ready to retry.');return;}
    const view=await readConversation(id);
    if(view.held_prompts.some(prompt=>prompt.sender_message_id===sending.message.messageId)){
      await finish(sending,'Queued for Chief');return;
    }
    // Missing receipts do not prove absence. Never issue a second POST for this attempt.
    note='Delivery is not confirmed. Your draft is kept. Check again or open Chief.';isError=true;
  }
  async function send():Promise<void>{
    if(busy || intakeBusy || !ready || voiceActive)return;
    busy=true;note='';isError=false;
    let requestSent=false;
    try{
      if(attempt){await checkDelivery(attempt);return;}
      if(!navigation)throw new Error('Chief messaging requires the application navigation owner.');
      const content=[...(text.trim()?[{piece:'text' as const,text:text.trim()}]:[]),...pendingImagesAsPieces(images),...pendingFilesAsPieces(files)];
      if(!content.length)return;
      const pointer=await readChiefPointer();
      const sending:ChiefMessageAttempt={expectedConversationId:pointer.conversation_id,message:mintOutgoingMessage({content,senderLabel:'owner',mode:'queue'})};
      await saveChiefAttempt(sending);attempt=sending;
      const effective=navigation.state.effective;
      requestSent=true;
      const delivered=await navigation.runCommand(conversationOwnerImpact(effective?navigation.presentationFor(effective):undefined,'chief','chief'),responseMetadata=>
        mutateJson<DeliveredMessage>('/api/chief/conversation/send',{method:'POST',responseMetadata,body:sendBodyFor({message:sending.message,current:{model:null,reasoningEffort:null},picked:{model:null,reasoningEffort:null},conversationId:sending.expectedConversationId})}),'send Chief message');
      if(delivered.fate==='started'){await finish(sending,'Sent to Chief');}
      else if(delivered.fate==='injected'){await finish(sending,'Sent to Chief’s current turn');}
      else if(delivered.fate==='queued'){await finish(sending,'Queued for Chief');}
      else if(delivered.fate==='uncertain'){
        // A first send can name its new destination even when delivery is uncertain.
        if(sending.expectedConversationId===null && delivered.conversation_id!==null){
          attempt={...sending,expectedConversationId:delivered.conversation_id};await saveChiefAttempt(attempt);
        }
        note='Delivery is uncertain. Your draft is kept. Do not resend.';isError=true;
      }else{await keepDraft(fateSentence(delivered) || 'The message did not reach Chief. Your draft is kept.');}
    }catch(problem){
      if(requestSent && problem instanceof PlannerFetchError && problem.status && problem.status>=400 && problem.status<500){
        try{if(attempt)await clearChiefAttempt();attempt=null;}catch(storageProblem){showError(storageProblem);return;}
      }
      showError(problem);
    }finally{busy=false;}
  }
</script>

<div data-review-chief-composer>
  <ReviewReplyInput bind:text bind:files bind:images bind:intakeBusy bind:voiceActive draftKey={chiefDraftKey}
    {disabled} sendDisabled={busy || !ready} sending={busy} preserveHeight allowEmpty={attempt!==null}
    placeholder="Message Chief…" label="Message Chief" sendLabel={attempt?'Check delivery':'Send message to Chief'} onSend={send} onError={showError}/>
  <div class="review-chief-receipt" class:error={isError} role={isError?'alert':'status'} aria-live="polite">
    <span>{note}</span>
    {#if note}<a href={workspaceAddress({kind:'chief'})}>Open Chief</a>{/if}
  </div>
</div>

<style>
  .review-chief-receipt{height:var(--space-7);overflow:auto;display:flex;align-items:flex-start;justify-content:space-between;gap:var(--space-3);padding-top:var(--space-2);color:var(--text-faint);font-size:var(--type-sm);line-height:1.5}
  .review-chief-receipt.error{color:var(--text-default)}
  .review-chief-receipt a{flex:none;color:var(--text-faint);text-underline-offset:var(--space-1)}
</style>
