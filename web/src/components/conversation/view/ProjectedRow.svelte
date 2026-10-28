<script lang="ts">
  import SkeletonRegion from "../../SkeletonRegion.svelte";
  import { DiagnosticExpiredError, DIAGNOSTIC_EXPIRED_MESSAGE } from "../../../lib/conversation/diagnosticExpiry";
  import { parseDeferredViewBody } from '../../../lib/markdown/workerClient';
  import type { ConversationViewRow, ViewRowBody, ViewBodyReference } from '../../../lib/conversation/view/contracts';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { rowForBody } from '../../../lib/conversation/view/presentation';
  import ConversationTranscript from '../ConversationTranscript.svelte';
  import ToolCallRow from '../ToolCallRow.svelte';
  import type { BackendModel } from '../../../lib/conversation/wire';
  import type { SvelteMap, SvelteSet } from 'svelte/reactivity';
  let { activityUncertain = false, item, reader, models, ownSenderLabel, openRows, wholeDetails, onEmptyEnding }: {
      activityUncertain?: boolean;
      item: ConversationViewRow;
      reader: ConversationViewReader;
      models: readonly BackendModel[];
      ownSenderLabel: string | null;
      openRows: SvelteSet<string>;
      wholeDetails: SvelteMap<string, string>;
      onEmptyEnding: (reference: ViewBodyReference) => void;
  } = $props();
  let deferred = $state.raw<{ body: ViewRowBody; reference: ViewBodyReference } | null>(null);
  let error = $state<string | null>(null);
  let progress = $state<string | null>(null);
  const presentationAttempt = $derived(reader.restorationAnchor?.key === item.key ? reader.restorationAttempt : 0);
  const body = $derived(item.body.kind === 'inline' ? item.body.value : deferred?.reference.body_id === item.body.reference.body_id && deferred.reference.version === item.body.reference.version ? deferred.body : null);
  const row = $derived(body === null ? null : rowForBody(item, body));
  $effect(() => { if (body !== null) { reader.textBodyReady(item.key, row && 'content' in row ? row.content : []); } });
  $effect(() => {
      if (item.body.kind === 'deferred' && body?.kind === 'turn_ended' && body.ending === 'completed' && body.automatic_compaction_result !== 'not_compacted')
          onEmptyEnding(item.body.reference);
  });
  $effect(() => {
      presentationAttempt;
      if (item.body.kind !== 'deferred')
          return;
      const reference = item.body.reference;
      const abort = new AbortController();
      deferred = null;
      error = null;
      void reader.body(reference, abort.signal).then(text => parseDeferredViewBody(text, abort.signal)).then(body => { if (!abort.signal.aborted)
          deferred = { body, reference }; }).catch(reason => { if (!abort.signal.aborted)
           { error = reason instanceof DiagnosticExpiredError ? DIAGNOSTIC_EXPIRED_MESSAGE : "This detail did not load."; reader.failTextRestoration(item.key, reason instanceof DiagnosticExpiredError); } });
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
          progress = text; }).catch(reason => { if (!abort.signal.aborted && reason instanceof DiagnosticExpiredError)
          error = DIAGNOSTIC_EXPIRED_MESSAGE; });
      return () => { abort.abort(); progress = null; };
  });
</script>
<SkeletonRegion shape="block" loading={row === null && !error} loadingText="Loading conversation detail…">
{#if row?.kind==='tool_call' && reader.lens==='full'}
  <div data-conversation-work-entries>{#if error}<p role="status">{error}</p>{/if}<ToolCallRow {activityUncertain} row={{...row,progress}} conversationId={reader.id} {openRows} {wholeDetails}/></div>
{:else if row!==null}
  {#key presentationAttempt}
  <ConversationTranscript {activityUncertain} rows={[]} visibleRows={[]} lens={reader.lens} conversationId={reader.id} {models} {ownSenderLabel} projectedItems={[{kind:'row',key:item.key,row,behindTheFoldOf:null}]}/>
  {/key}
{:else}
  <div aria-busy={!error} role="status">{error ?? ""}</div>
{/if}

</SkeletonRegion>
