<script lang="ts">
  import {onMount,untrack} from 'svelte';
  import ComposerField from './conversation/ComposerField.svelte';
  import {readComposerDraft,writeComposerDraft} from '../lib/conversation/composerDraft';
  import {createPendingConversationFiles,pendingConversationFileBytes,CONVERSATION_FILE_ACCEPT,type PendingConversationFile} from '../lib/conversation/pendingFiles';
  import {createPendingConversationImages,releasePendingImages,type PendingConversationImage} from '../lib/conversation/pendingImages';
  import {createVoiceCapture,voiceCaptureSupported,type VoiceCapture,type VoiceCaptureState} from '../lib/conversation/voiceCapture';
  import {attachmentByteLabel,attachmentExtension} from './conversation/composer/attachments';
  let {text=$bindable(''),files=$bindable([]),images=$bindable([]),intakeBusy=$bindable(false),voiceActive=$bindable(false),draftKey,disabled=false,sendDisabled=false,sending=false,placeholder='Reply…',label='Reply',sendLabel='Send reply',preserveHeight=false,allowEmpty=false,onSend,onError}: {
    text?:string;files?:PendingConversationFile[];images?:PendingConversationImage[];intakeBusy?:boolean;voiceActive?:boolean;draftKey:string;disabled?:boolean;sendDisabled?:boolean;sending?:boolean;placeholder?:string;label?:string;sendLabel?:string;preserveHeight?:boolean;allowEmpty?:boolean;onSend:()=>Promise<void>|void;onError:(error:unknown)=>void;
  }=$props();
  text=untrack(()=>text || readComposerDraft(draftKey));
  let fileInput=$state<HTMLInputElement|null>(null);
  let root=$state<HTMLDivElement|null>(null);
  let input=$state<HTMLTextAreaElement|null>(null);
  let held=$state<{box:number;input:number}|null>(null);
  let voiceState=$state<VoiceCaptureState>({phase:'idle'});
  let voice:VoiceCapture|null=null;
  let voiceSupported=$state(false);
  let nextId=0;
  let hasReply=$derived(Boolean(text.trim() || files.length || images.length));
  $effect(()=>{writeComposerDraft(draftKey,text);});
  $effect(()=>{voiceActive=voiceState.phase!=='idle';});
  onMount(()=>{
    voiceSupported=voiceCaptureSupported();
    voice=createVoiceCapture({onState:state=>voiceState=state,onTranscript:transcript=>{if(transcript)text=text.trim()?`${text.trim()}\n\n${transcript}`:transcript;}});
    return()=>{voice?.dispose();releasePendingImages(images);};
  });
  async function submit():Promise<void>{
    if(sendDisabled || intakeBusy || (!hasReply && !allowEmpty) || voiceState.phase!=='idle')return;
    if(preserveHeight)held={box:Math.max(held?.box??0,root?.getBoundingClientRect().height??0),input:Math.max(held?.input??0,input?.getBoundingClientRect().height??0)};
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

<!-- The composer's field (ComposerField): the + attaches pictures and files, the mic speaks a
     reply, and the arrow sends it, as Cmd/Ctrl+Enter does. -->
<div class="review-reply" bind:this={root} data-review-voice={voiceState.phase}>
  <input bind:this={fileInput} hidden type="file" multiple accept={`image/png,image/jpeg,image/gif,image/webp,${CONVERSATION_FILE_ACCEPT}`} onchange={()=>void attach(fileInput?.files??null)}/>
  <ComposerField bind:value={text} bind:input {placeholder} {label} {disabled} {held}
    inputAttributes={{'data-review-reply':''}}
    onkeydown={event=>{if(event.key==='Enter'&&(event.metaKey||event.ctrlKey)){event.preventDefault();void submit();}}}
    attach={{label:'Attach a file',onclick:()=>fileInput?.click(),attributes:{'data-review-attach':''}}}
    record={{label:'Speak a reply',disabled:!voiceSupported,onclick:()=>void voice?.startRecording(),attributes:{'data-voice-record':''}}}
    send={{label:sendLabel,ready:hasReply,sending,disabled:sendDisabled || intakeBusy || (!hasReply && !allowEmpty),onclick:()=>void submit(),attributes:{'data-review-send':''}}}
    voice={{state:voiceState,cancel:()=>voice?.cancel(),stop:()=>voice?.stopRecording(),retry:()=>voice?.retry()}}
    attachments={images.length || files.length ? attached : undefined}/>
</div>

{#snippet attached()}
  <!-- The composer's own chips: a picture as itself, a file by its kind, name and size. -->
  <div class="chat-attachments" aria-label="Attached and waiting to send">
    {#each images as image (image.id)}
      <div class="chat-att img review-attachment" title={`${image.fileName} · ${image.mediaType} · ${attachmentByteLabel(image.byteCount)}`}>
        <img src={image.previewUrl} alt={image.fileName}/>
        <button type="button" class="chat-att-x" aria-label={`Remove ${image.fileName}`} title="Remove" {disabled} onclick={()=>removeImage(image.id)}>×</button>
      </div>
    {/each}
    {#each files as file (file.id)}
      <div class="chat-att file review-attachment" title={`${file.fileName} · ${file.mediaType} · ${attachmentByteLabel(file.byteCount)}`}>
        <span class="chat-att-kind" aria-hidden="true">{attachmentExtension(file.fileName)}</span>
        <span class="chat-att-words">
          <span class="chat-att-name">{file.fileName}</span>
          <span class="chat-att-meta">{file.mediaType} · {attachmentByteLabel(file.byteCount)}</span>
        </span>
        <button type="button" class="chat-att-x" aria-label={`Remove ${file.fileName}`} title="Remove" {disabled} onclick={()=>files=files.filter(entry=>entry.id!==file.id)}>×</button>
      </div>
    {/each}
  </div>
{/snippet}

<style>
  .review-reply{margin-top:var(--space-4)}
</style>
