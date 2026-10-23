<script lang="ts">
  import UserMessage from '../UserMessage.svelte';
  import SkeletonRegion from '../../SkeletonRegion.svelte';
  import { parseDeferredViewBody } from '../../../lib/markdown/workerClient';
  import { rowForBody } from '../../../lib/conversation/view/presentation';
  import type { ConversationViewRow, ViewRowBody } from '../../../lib/conversation/view/contracts';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import type { OutgoingMessage } from '../../../lib/conversation/outgoing';
  let { item, local, reader, ownSenderLabel, onStopDrawingMessage, onSendAgain }: {
    item: ConversationViewRow | null; local: OutgoingMessage | null; reader: ConversationViewReader; ownSenderLabel: string | null;
    onStopDrawingMessage?: (id:string)=>Promise<void>|void; onSendAgain?: (id:string)=>Promise<void>|void;
  } = $props();
  let deferred = $state.raw<{version:string; body:ViewRowBody}|null>(null);
  let error = $state<string|null>(null);
  const version = $derived(item?.body.kind==='deferred' ? JSON.stringify(item.body.reference) : '');
  const body = $derived(item?.body.kind==='inline' ? item.body.value : deferred?.version===version ? deferred.body : null);
  const row = $derived(item && body ? rowForBody(item,body) : null);
  const prompt = $derived(row && (row.kind==='prompt'||row.kind==='prompt_refused'||row.kind==='prompt_uncertain'||row.kind==='prompt_discarded') ? row : null);
  $effect(()=>{
    if (item?.body.kind!=='deferred') return;
    const reference=item.body.reference; const requestedVersion=version; const abort=new AbortController(); error=null;
    void reader.body(reference,abort.signal).then(text=>parseDeferredViewBody(text,abort.signal)).then(body=>{if(!abort.signal.aborted)deferred={version:requestedVersion,body};}).catch(()=>{if(!abort.signal.aborted){error='This message did not load.'; if(reader.entryTarget?.key===item?.key)reader.failEntry();}});
    return ()=>abort.abort();
  });
  $effect(()=>{if(item && body)reader.entryBodyReady(item.key);});
</script>
<SkeletonRegion shape="block" loading={!prompt&&!local&&!error} loadingText="Loading conversation detail…">
  {#if prompt || local}<UserMessage row={prompt} {local} conversationId={reader.id} {ownSenderLabel} {onStopDrawingMessage} {onSendAgain}/>{:else if error}<p role="status">{error}</p>{/if}
</SkeletonRegion>
