<script lang="ts">
  import {onMount,untrack} from 'svelte';
  import {readComposerDraft,writeComposerDraft} from '../lib/conversation/composerDraft';
  import {createPendingConversationFiles,pendingConversationFileBytes,CONVERSATION_FILE_ACCEPT,type PendingConversationFile} from '../lib/conversation/pendingFiles';
  import {createPendingConversationImages,releasePendingImages,type PendingConversationImage} from '../lib/conversation/pendingImages';
  import {createVoiceCapture,formatVoiceTime,voiceCaptureSupported,type VoiceCapture,type VoiceCaptureState} from '../lib/conversation/voiceCapture';
  import {attachmentByteLabel} from './conversation/composer/attachments';
  let {text=$bindable(''),files=$bindable([]),images=$bindable([]),intakeBusy=$bindable(false),voiceActive=$bindable(false),draftKey,disabled=false,sendDisabled=false,sending=false,placeholder='Reply…',label='Reply',sendLabel='Send reply',preserveHeight=false,allowEmpty=false,onSend,onError}: {
    text?:string;files?:PendingConversationFile[];images?:PendingConversationImage[];intakeBusy?:boolean;voiceActive?:boolean;draftKey:string;disabled?:boolean;sendDisabled?:boolean;sending?:boolean;placeholder?:string;label?:string;sendLabel?:string;preserveHeight?:boolean;allowEmpty?:boolean;onSend:()=>Promise<void>|void;onError:(error:unknown)=>void;
  }=$props();
  text=untrack(()=>text || readComposerDraft(draftKey));
  let input=$state<HTMLTextAreaElement|null>(null);
  let fileInput=$state<HTMLInputElement|null>(null);
  let root=$state<HTMLDivElement|null>(null);
  let heldHeight=$state(0);
  let heldInputHeight=$state(44);
  let voiceState=$state<VoiceCaptureState>({phase:'idle'});
  let voice:VoiceCapture|null=null;
  let voiceSupported=$state(false);
  let nextId=0;
  let hasReply=$derived(Boolean(text.trim() || files.length || images.length));
  $effect(()=>{writeComposerDraft(draftKey,text);});
  $effect(()=>{voiceActive=voiceState.phase!=='idle';});
  $effect(()=>{text;if(input){input.style.height='auto';input.style.height=`${Math.min(Math.max(input.scrollHeight,heldInputHeight),220)}px`;}});
  onMount(()=>{
    voiceSupported=voiceCaptureSupported();
    voice=createVoiceCapture({onState:state=>voiceState=state,onTranscript:transcript=>{if(transcript)text=text.trim()?`${text.trim()}\n\n${transcript}`:transcript;}});
    return()=>{voice?.dispose();releasePendingImages(images);};
  });
  async function submit():Promise<void>{
    if(sendDisabled || intakeBusy || (!hasReply && !allowEmpty) || voiceState.phase!=='idle')return;
    if(preserveHeight){heldHeight=Math.max(heldHeight,root?.getBoundingClientRect().height??0);heldInputHeight=Math.max(heldInputHeight,input?.getBoundingClientRect().height??44);}
    await onSend();
  }
  async function attach(chosen:FileList|null):Promise<void>{
    if(!chosen || disabled)return;
    intakeBusy=true;onError(null);
    try{
      const picked=[...chosen];
      nextId=Math.max(nextId,...images.map(image=>image.id+1),...files.map(file=>file.id+1));
      const pictureFiles=picked.filter(file=>file.type.startsWith('image/'));
      const pictureResult=await createPendingConversationImages(pictureFiles,nextId,images.reduce((sum,image)=>sum+image.byteCount,0));
      images=[...images,...pictureResult.accepted];nextId=pictureResult.nextId;
      const result=await createPendingConversationFiles(picked.filter(file=>!pictureFiles.includes(file)),nextId,pendingConversationFileBytes(files));
      files=[...files,...result.accepted];nextId=result.nextId;
      if(result.rejected.length || pictureResult.rejected.length)onError(new Error('Some attachments have an unsupported format or exceed the attachment limit.'));
    }catch(problem){onError(problem);}finally{intakeBusy=false;if(fileInput)fileInput.value='';}
  }
  function removeImage(id:number){const removed=images.filter(image=>image.id===id);images=images.filter(image=>image.id!==id);releasePendingImages(removed);}
</script>
        <div class="review-reply" bind:this={root} class:held={preserveHeight && heldHeight>0} style:min-height={heldHeight ? `${heldHeight}px` : undefined} data-review-voice={voiceState.phase}>
          {#if voiceState.phase!=='idle'}
            <div class="review-voice">
              {#if voiceState.phase==='recording'}<span class="live-text-shimmer">recording</span><span>{formatVoiceTime(voiceState.elapsedMs)}</span>
                <button data-voice-stop aria-label="Stop recording and transcribe" onclick={()=>voice?.stopRecording()}>■</button>
              {:else if voiceState.phase==='transcribing'}<span class="live-text-shimmer">transcribing</span>
              {:else}<span>transcription failed · kept {formatVoiceTime(voiceState.keptMs)}</span><button data-voice-retry onclick={()=>voice?.retry()}>retry</button>{/if}
              <button data-voice-cancel aria-label="Cancel recording" onclick={()=>voice?.cancel()}>×</button>
            </div>
          {:else}
            <textarea bind:this={input} bind:value={text} data-review-reply rows="1" {placeholder} aria-label={label} disabled={disabled} onkeydown={event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();void submit();}}}></textarea>
            {#if images.length || files.length}<div class="review-attachments">
              {#each images as image(image.id)}<span class="review-attachment">{image.fileName} · {attachmentByteLabel(image.byteCount)}<button aria-label={`Remove ${image.fileName}`} disabled={disabled} onclick={()=>removeImage(image.id)}>×</button></span>{/each}
              {#each files as file(file.id)}<span class="review-attachment">{file.fileName} · {attachmentByteLabel(file.byteCount)}<button aria-label={`Remove ${file.fileName}`} disabled={disabled} onclick={()=>files=files.filter(entry=>entry.id!==file.id)}>×</button></span>{/each}
            </div>{/if}
            <div class="review-reply-tools">
              <input bind:this={fileInput} hidden type="file" multiple accept={`image/png,image/jpeg,image/gif,image/webp,${CONVERSATION_FILE_ACCEPT}`} onchange={()=>void attach(fileInput?.files??null)}/>
              <button class="review-tool" data-review-attach aria-label="Attach a file" disabled={disabled} onclick={()=>fileInput?.click()}><svg viewBox="0 0 24 24"><path d="M8 12.5l6.8-6.8a3 3 0 0 1 4.2 4.2l-8.2 8.2a5 5 0 0 1-7.1-7.1l8-8"/></svg></button>
              <button class="review-tool" data-voice-record aria-label="Speak a reply" disabled={disabled || !voiceSupported} onclick={()=>void voice?.startRecording()}><svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/></svg></button>
              <button class="review-send" class:ready={hasReply} data-review-send aria-label={sendLabel} aria-busy={sending} disabled={sendDisabled || intakeBusy || (!hasReply && !allowEmpty)} onclick={()=>void submit()}>
                {#if sending}<span class="review-send-spinner"></span>{:else}<svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7"/></svg>{/if}
              </button>
            </div>
          {/if}
        </div>

<style>
  .review-reply{margin-top:var(--space-4);background:var(--surface-recessed);border-radius:var(--radius-md);box-shadow:inset 0 1px 2px rgb(0 0 0 / 35%);padding:var(--space-3)}
  .review-reply.held{display:flex;flex-direction:column}
  .held .review-reply-tools{margin-top:auto;padding-top:var(--space-2)}
  textarea{width:100%;background:none;border:0;resize:none;color:var(--text-strong);font:inherit;font-size:var(--type-md);line-height:1.55;outline:none;min-height:44px;padding:0;overflow-y:auto;display:block;caret-color:var(--accent-bright)}
  textarea::placeholder{color:var(--text-faintest)}
  .review-reply-tools{display:flex;align-items:center;gap:var(--space-1);margin-top:var(--space-2)}
  .review-tool{width:32px;height:32px;display:grid;place-items:center;background:none;border:0;color:var(--text-faintest);cursor:pointer;padding:0;border-radius:var(--radius-md)}
  .review-tool svg{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.35;stroke-linecap:round;stroke-linejoin:round}
  .review-send{margin-left:auto;width:32px;height:32px;flex:none;padding:0;border:0;border-radius:50%;background:var(--surface-2);color:var(--text-faintest);cursor:pointer;display:grid;place-items:center}
  .review-send.ready{background:var(--accent-bright);color:var(--surface-ink)}
  .review-send svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
  button:disabled{opacity:.5;cursor:default}
  .review-tool:hover:not(:disabled){background-image:var(--interaction-hover);color:var(--text-strong)}
  .review-send-spinner{width:14px;height:14px;border-radius:50%;border:2px solid color-mix(in srgb,var(--surface-ink) 30%,transparent);border-top-color:var(--surface-ink);animation:spin var(--motion-loop-spin) linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  .review-attachments{display:flex;gap:var(--space-2);flex-wrap:wrap;margin-top:var(--space-2)}
  .review-attachment{display:inline-flex;gap:var(--space-2);align-items:center;border:var(--border-hairline) solid var(--border-color);padding:var(--space-1) var(--space-2);border-radius:var(--radius-md);font:var(--type-xs) var(--font-mono);overflow-wrap:anywhere;max-width:100%}
  .review-attachment button{background:none;border:0;color:var(--text-faintest);cursor:pointer}
  .review-voice{display:flex;gap:var(--space-2);align-items:center;min-height:44px}
  .review-voice button{background:none;border:0;color:var(--text-strong);cursor:pointer;margin-left:auto}
</style>
