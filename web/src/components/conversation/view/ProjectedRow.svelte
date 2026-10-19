<script lang="ts">
  import { parseDeferredViewBody } from '../../../lib/markdown/workerClient';
  import type { ConversationViewRow, ViewRowBody } from '../../../lib/conversation/view/contracts';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { rowForBody } from '../../../lib/conversation/view/presentation';
  import ConversationTranscript from '../ConversationTranscript.svelte';
  import ToolCallRow from '../ToolCallRow.svelte';
  import type { BackendModel } from '../../../lib/conversation/wire';
  import type { SvelteMap, SvelteSet } from 'svelte/reactivity';
  let { item, reader, models, ownSenderLabel, openRows, wholeDetails }: {
      item: ConversationViewRow;
      reader: ConversationViewReader;
      models: readonly BackendModel[];
      ownSenderLabel: string | null;
      openRows: SvelteSet<string>;
      wholeDetails: SvelteMap<string, string>;
  } = $props();
  let deferred = $state.raw<ViewRowBody | null>(null);
  let error = $state<string | null>(null);
  let progress = $state<string | null>(null);
  const body = $derived(item.body.kind === 'inline' ? item.body.value : deferred);
  const row = $derived(body === null ? null : rowForBody(item, body));
  $effect(() => {
      if (item.body.kind !== 'deferred')
          return;
      const reference = item.body.reference;
      const abort = new AbortController();
      deferred = null;
      error = null;
      void reader.body(reference, abort.signal).then(text => parseDeferredViewBody(text, abort.signal)).then(body => { if (!abort.signal.aborted)
          deferred = body; }).catch(reason => { if (!abort.signal.aborted)
          error = String(reason); });
      return () => { abort.abort(); deferred = null; };
  });
  $effect(() => {
      const reference = reader.status?.live_tool_progress;
      if (row?.kind !== 'tool_call' || !openRows.has(row.toolCallId) || reader.status?.live_tool_key !== row.toolCallId || !reference) {
          progress = null;
          return;
      }
      const abort = new AbortController();
      void reader.body(reference, abort.signal).then(text => { if (!abort.signal.aborted)
          progress = text; }).catch(() => undefined);
      return () => { abort.abort(); progress = null; };
  });
</script>
{#if row?.kind==='tool_call'}
  <div class="c2-run" data-conversation-work-entries><ToolCallRow row={{...row,progress}} conversationId={reader.id} {openRows} {wholeDetails}/></div>
{:else if row!==null}
  <ConversationTranscript rows={[]} visibleRows={[]} lens={reader.lens} conversationId={reader.id} {models} {ownSenderLabel} projectedItems={[{kind:'row',key:item.key,row,behindTheFoldOf:null}]}/>
{:else}
  <div aria-busy={!error} role="status">{error??'Loading…'}</div>
{/if}
