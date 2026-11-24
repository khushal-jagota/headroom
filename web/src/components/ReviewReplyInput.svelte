<script lang="ts">
  import {onMount,untrack} from 'svelte';
  import ComposerField from './conversation/ComposerField.svelte';
  import {readComposerDraft,writeComposerDraft} from '../lib/conversation/composerDraft';
  import type {PendingAttachment, UploadDestination} from '../lib/conversation/videoUploads/contracts';
  import type {PendingConversationFile} from '../lib/conversation/pendingFiles';
  import {releasePendingImages,type PendingConversationImage} from '../lib/conversation/pendingImages';
  import {createVoiceCapture,voiceCaptureSupported,type VoiceCapture,type VoiceCaptureState} from '../lib/conversation/voiceCapture';
  let {uploads=$bindable([]),uploadDestination,text=$bindable(''),files=$bindable([]),images=$bindable([]),intakeBusy=$bindable(false),voiceActive=$bindable(false),draftKey,disabled=false,sendDisabled=false,sending=false,placeholder='Reply…',label='Reply',sendLabel='Send reply',preserveHeight=false,allowEmpty=false,onSend,onError}: {
    uploads?:PendingAttachment[];uploadDestination:UploadDestination;text?:string;files?:PendingConversationFile[];images?:PendingConversationImage[];intakeBusy?:boolean;voiceActive?:boolean;draftKey:string;disabled?:boolean;sendDisabled?:boolean;sending?:boolean;placeholder?:string;label?:string;sendLabel?:string;preserveHeight?:boolean;allowEmpty?:boolean;onSend:()=>Promise<void>|void;onError:(error:unknown)=>void;
  }=$props();
  text=untrack(()=>text || readComposerDraft(draftKey));
  let root=$state<HTMLDivElement|null>(null);
  let input=$state<HTMLTextAreaElement|null>(null);
  let held=$state<{box:number;input:number}|null>(null);
  let voiceState=$state<VoiceCaptureState>({phase:'idle'});
  let voice:VoiceCapture|null=null;
  let voiceSupported=$state(false);
  let hasReply=$derived(Boolean(text.trim() || files.length || images.length || uploads.length));
  $effect(()=>{writeComposerDraft(draftKey,text);});
  $effect(()=>{voiceActive=voiceState.phase!=='idle';});
  onMount(()=>{
    voiceSupported=voiceCaptureSupported();
    voice=createVoiceCapture({onState:state=>voiceState=state,onTranscript:transcript=>{if(transcript)text=text.trim()?`${text.trim()}\n\n${transcript}`:transcript;}});
    return()=>{voice?.dispose();releasePendingImages(images);};
  });
  async function submit():Promise<void>{
    if(sendDisabled || (intakeBusy && !allowEmpty) || (!hasReply && !allowEmpty) || voiceState.phase!=='idle')return;
    if(preserveHeight)held={box:Math.max(held?.box??0,root?.getBoundingClientRect().height??0),input:Math.max(held?.input??0,input?.getBoundingClientRect().height??0)};
    await onSend();
  }

</script>

<!-- The composer's field (ComposerField): the + attaches pictures and files, the mic speaks a
     reply, and the arrow sends it, as Enter does (Shift+Enter adds a line). -->
<div class="review-reply" bind:this={root} data-review-voice={voiceState.phase}>
  <ComposerField {draftKey} {uploadDestination} bind:uploads bind:value={text} bind:input bind:images bind:files bind:intakeBusy {onError} {placeholder} {label} {disabled} {held}
    inputAttributes={{'data-review-reply':''}}
    onsubmit={()=>void submit()}
    record={{label:'Speak a reply',disabled:!voiceSupported,onclick:()=>void voice?.startRecording(),attributes:{'data-voice-record':''}}}
    send={{label:sendLabel,ready:hasReply || allowEmpty,sending,checkingReceipt:allowEmpty,disabled:sendDisabled || (intakeBusy && !allowEmpty) || (!hasReply && !allowEmpty),onclick:()=>void submit(),attributes:{'data-review-send':''}}}
    voice={{state:voiceState,cancel:()=>voice?.cancel(),stop:()=>voice?.stopRecording(),retry:()=>voice?.retry()}}/>
</div>

<style>
  .review-reply{margin-top:var(--space-4)}
</style>
