<script lang="ts">
  import SkeletonRegion from "../../SkeletonRegion.svelte";
  import { DiagnosticExpiredError, DIAGNOSTIC_EXPIRED_MESSAGE } from "../../../lib/conversation/diagnosticExpiry";
  import { parseDeferredViewBody } from '../../../lib/markdown/workerClient';
  import type { ConversationViewRow, ViewRowBody, ViewBodyReference } from '../../../lib/conversation/view/contracts';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { rowForBody } from '../../../lib/conversation/view/presentation';
  import ConversationTranscript from '../ConversationTranscript.svelte';
  import ConversationCredentialRequestCard from '../ConversationCredentialRequestCard.svelte';
  import { getContext } from 'svelte';
  import { DOCKED_CREDENTIAL_REQUEST } from '../../../lib/conversation/composer';
  import ToolCallRow from '../ToolCallRow.svelte';
  import TranscriptStatusRow from '../TranscriptStatusRow.svelte';
  import { turnEndingStatus, TURN_STOPPED_STATUS } from '../../../lib/conversation/transcript';
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
  const dockedCredential = getContext<(() => string | null) | undefined>(DOCKED_CREDENTIAL_REQUEST);
  const row = $derived(body === null ? null : rowForBody(item, body));
  /** v6: in Full, prose that is not its turn's final message is narration; while the turn is
   *  live, all of its prose is. */
  const narration = $derived.by(() => {
      if (item.row_kind !== 'agent_message' || item.turn_key === null) return null;
      for (const page of reader.pages)
          for (const each of page.items)
              if (each.kind === 'turn' && each.key === item.turn_key)
                  return !each.settled || each.latest_message_key !== item.key;
      return null;
  });
  /** How long this ending's turn ran, from its head in the pages held. */
  const turnDuration = $derived.by(() => {
      if (item.turn_key === null) return null;
      for (const page of reader.pages)
          for (const each of page.items)
              if (each.kind === 'turn' && each.key === item.turn_key)
                  return each.duration_seconds;
      return null;
  });
  // A turn's ending and a turn that stopped without one are status lines, drawn in the
  // same family as a message that was not delivered. A stopped turn's one line carries
  // its length ("Stopped after 8m 45s"); its head is not drawn (ProjectedTranscript).
  const endingStatus = $derived(row?.kind === 'turn_ended' && row.automaticCompactionResult !== 'not_compacted' ? turnEndingStatus(row.ending, row.errorSummary, turnDuration) : row?.kind === 'turn_stopped' ? TURN_STOPPED_STATUS : null);
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
{#if row?.kind==='tool_call'}
  <div data-conversation-work-entries>{#if error}<p role="status">{error}</p>{/if}<ToolCallRow {activityUncertain} row={{...row,progress}} conversationId={reader.id} {openRows} {wholeDetails}/></div>
{:else if row !== null && endingStatus !== null}
  <TranscriptStatusRow status={endingStatus} role={endingStatus.tone === 'error' ? 'alert' : 'status'} data-conversation-row={row.kind} data-conversation-turn-ending={row.kind === 'turn_ended' ? row.ending : undefined}/>
{:else if row?.kind==='credential_request'}
  <!-- The pending request the composer asks is drawn there, once. -->
  {#if row.request.request_id !== (dockedCredential?.() ?? null)}<ConversationCredentialRequestCard request={row.request}/>{/if}
{:else if row!==null}
  {#key presentationAttempt}
  <ConversationTranscript {activityUncertain} rows={[]} visibleRows={[]} lens={reader.lens} conversationId={reader.id} {models} {ownSenderLabel} {narration} projectedItems={[{kind:'row',key:item.key,row,behindTheFoldOf:null}]}/>
  {/key}
{:else}
  <div aria-busy={!error} role="status">{error ?? ""}</div>
{/if}

</SkeletonRegion>
