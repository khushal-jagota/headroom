<script lang="ts">
  import {onMount} from 'svelte';
  import ComposerField from './conversation/ComposerField.svelte';
  import type {PendingAttachment, UploadDestination} from '../lib/conversation/videoUploads/contracts';
  import type {PendingConversationFile} from '../lib/conversation/pendingFiles';
  import type {PendingConversationImage} from '../lib/conversation/pendingImages';
  import {createVoiceCapture,voiceCaptureSupported,type VoiceCapture,type VoiceCaptureState} from '../lib/conversation/voiceCapture';
  let {uploads=$bindable([]),uploadDestination,text=$bindable(''),files=$bindable([]),images=$bindable([]),intakeBusy=$bindable(false),voiceActive=$bindable(false),draftKey,attachmentDisabled=false,sendDisabled=false,placeholder='Reply…',label='Reply',sendLabel='Send reply',preserveHeight=false,sending=false,result,sendTitle,onSend,onError}: {
    uploads?:PendingAttachment[];uploadDestination:UploadDestination;text?:string;files?:PendingConversationFile[];images?:PendingConversationImage[];intakeBusy?:boolean;voiceActive?:boolean;draftKey:string;attachmentDisabled?:boolean;sendDisabled?:boolean;placeholder?:string;label?:string;sendLabel?:string;preserveHeight?:boolean;sending?:boolean;result?:'sent'|'failed'|'unsure';sendTitle?:string;onSend:()=>Promise<void>|void;onError:(error:unknown)=>void;
  }=$props();
  let root=$state<HTMLDivElement|null>(null);
  let input=$state<HTMLTextAreaElement|null>(null);
  let held=$state<{box:number;input:number}|null>(null);
  let voiceState=$state<VoiceCaptureState>({phase:'idle'});
  let voice:VoiceCapture|null=null;
  let voiceSupported=$state(false);
  let hasReply=$derived(Boolean(text.trim() || files.length || images.length || uploads.length));
  $effect(()=>{voiceActive=voiceState.phase!=='idle';});
  onMount(()=>{
    voiceSupported=voiceCaptureSupported();
    voice=createVoiceCapture({onState:state=>voiceState=state,onTranscript:transcript=>{if(transcript)text=text.trim()?`${text.trim()}\n\n${transcript}`:transcript;}});
    return()=>voice?.dispose();
  });
  export function focus(): void { input?.focus(); }
  async function submit():Promise<void>{
    if(sendDisabled || !hasReply || voiceState.phase!=='idle')return;
    if(preserveHeight)held={box:Math.max(held?.box??0,root?.getBoundingClientRect().height??0),input:Math.max(held?.input??0,input?.getBoundingClientRect().height??0)};
    await onSend();
    // A sent reply clears the draft: let go of the held size so the box shrinks back. A failed send keeps its text and its size.
    if(!text && !files.length && !images.length && !uploads.length)held=null;
  }

</script>

<!-- The composer's field (ComposerField): the + attaches pictures and files, the mic speaks a
     reply, and the arrow sends it, as Enter does (Shift+Enter adds a line). -->
<div class="review-reply" bind:this={root} data-review-voice={voiceState.phase}>
  <ComposerField {draftKey} {uploadDestination} bind:uploads bind:value={text} bind:input bind:images bind:files bind:intakeBusy {onError} {placeholder} {label} {attachmentDisabled} {held}
    inputAttributes={{'data-review-reply':''}}
    onsubmit={()=>void submit()}
    record={{label:'Speak a reply',disabled:!voiceSupported,onclick:()=>void voice?.startRecording(),attributes:{'data-voice-record':''}}}
    send={{label:sendTitle??sendLabel,ready:hasReply,sending,result,allowWhileIntake:true,disabled:sendDisabled || !hasReply,onclick:()=>void submit(),attributes:{'data-review-send':'',...(sendTitle?{title:sendTitle}:{})}}}
    voice={{state:voiceState,cancel:()=>voice?.cancel(),stop:()=>voice?.stopRecording(),retry:()=>voice?.retry()}}/>
</div>

<style>
  .review-reply{margin-top:var(--space-4)}
</style>
