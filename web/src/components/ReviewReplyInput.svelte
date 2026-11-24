<script lang="ts">
  import {onMount} from 'svelte';
  import ComposerField from './conversation/ComposerField.svelte';
  import {CONVERSATION_FILE_ACCEPT,type PendingConversationFile} from '../lib/conversation/pendingFiles';
  import type { PendingConversationImage } from '../lib/conversation/pendingImages';
  import {takePendingAttachments,ATTACHMENT_REJECTED} from '../lib/conversation/pendingAttachments';
  import AttachmentChips from './conversation/AttachmentChips.svelte';
  import {createVoiceCapture,voiceCaptureSupported,type VoiceCapture,type VoiceCaptureState} from '../lib/conversation/voiceCapture';
  let {text=$bindable(''),files=$bindable([]),images=$bindable([]),intakeBusy=$bindable(false),voiceActive=$bindable(false),attachmentDisabled=false,sendDisabled=false,placeholder='Reply…',label='Reply',sendLabel='Send reply',preserveHeight=false,onSend,onError}: {
    text?:string;files?:PendingConversationFile[];images?:PendingConversationImage[];intakeBusy?:boolean;voiceActive?:boolean;attachmentDisabled?:boolean;sendDisabled?:boolean;placeholder?:string;label?:string;sendLabel?:string;preserveHeight?:boolean;onSend:()=>Promise<void>|void;onError:(error:unknown)=>void;
  }=$props();
  let fileInput=$state<HTMLInputElement|null>(null);
  let root=$state<HTMLDivElement|null>(null);
  let input=$state<HTMLTextAreaElement|null>(null);
  let held=$state<{box:number;input:number}|null>(null);
  let voiceState=$state<VoiceCaptureState>({phase:'idle'});
  let voice:VoiceCapture|null=null;
  let voiceSupported=$state(false);
  let hasReply=$derived(Boolean(text.trim() || files.length || images.length));
  $effect(()=>{voiceActive=voiceState.phase!=='idle';});
  onMount(()=>{
    voiceSupported=voiceCaptureSupported();
    voice=createVoiceCapture({onState:state=>voiceState=state,onTranscript:transcript=>{if(transcript)text=text.trim()?`${text.trim()}\n\n${transcript}`:transcript;}});
    return()=>{voice?.dispose();};
  });
  export function focus(): void { input?.focus(); }
  async function submit():Promise<void>{
    if(sendDisabled || intakeBusy || !hasReply || voiceState.phase!=='idle')return;
    if(preserveHeight)held={box:Math.max(held?.box??0,root?.getBoundingClientRect().height??0),input:Math.max(held?.input??0,input?.getBoundingClientRect().height??0)};
    await onSend();
  }
  async function attach(chosen:FileList|null):Promise<void>{
    if(!chosen || attachmentDisabled)return;
    intakeBusy=true;onError(null);
    try{
      const taken=await takePendingAttachments([...chosen],{images,files});
      images=taken.images;files=taken.files;
      if(taken.rejected)onError(new Error(ATTACHMENT_REJECTED));
    }catch(problem){onError(problem);}finally{intakeBusy=false;if(fileInput)fileInput.value='';}
  }
</script>

<!-- The composer's field (ComposerField): the + attaches pictures and files, the mic speaks a
     reply, and the arrow sends it, as Enter does (Shift+Enter adds a line). -->
<div class="review-reply" bind:this={root} data-review-voice={voiceState.phase}>
  <input bind:this={fileInput} hidden type="file" multiple accept={`image/png,image/jpeg,image/gif,image/webp,${CONVERSATION_FILE_ACCEPT}`} onchange={()=>void attach(fileInput?.files??null)}/>
  <ComposerField bind:value={text} bind:input {placeholder} {label} {held}
    inputAttributes={{'data-review-reply':''}}
    onsubmit={()=>void submit()}
    attach={{label:'Attach a file',disabled:attachmentDisabled,onclick:()=>fileInput?.click(),attributes:{'data-review-attach':''}}}
    record={{label:'Speak a reply',disabled:!voiceSupported,onclick:()=>void voice?.startRecording(),attributes:{'data-voice-record':''}}}
    send={{label:sendLabel,ready:hasReply,sending:false,disabled:sendDisabled || intakeBusy || !hasReply,onclick:()=>void submit(),attributes:{'data-review-send':''}}}
    voice={{state:voiceState,cancel:()=>voice?.cancel(),stop:()=>voice?.stopRecording(),retry:()=>voice?.retry()}}
    attachments={images.length || files.length ? attached : undefined}/>
</div>

{#snippet attached()}
  <AttachmentChips bind:images bind:files disabled={attachmentDisabled}/>
{/snippet}

<style>
  .review-reply{margin-top:var(--space-4)}
</style>
