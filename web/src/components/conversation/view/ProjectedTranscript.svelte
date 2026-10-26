<script lang="ts">
  import { MessageExits } from '../../../lib/conversation/messageExits';
  import { reconcileDisplaySlots, isMessageRow, type MessageDisplaySlot } from '../../../lib/conversation/displaySlots';
  import type { OutgoingMessage } from '../../../lib/conversation/outgoing';
  import ProjectedMessage from './ProjectedMessage.svelte';
  import { untrack } from 'svelte';
  import { SvelteMap } from 'svelte/reactivity';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { compareItems } from '../../../lib/conversation/view/reader.svelte';
  import type { ConversationViewItem, ViewHistoryScope, ViewPageCursor } from '../../../lib/conversation/view/contracts';
  import type { BackendModel } from '../../../lib/conversation/wire';
  import TurnAnchor from '../TurnAnchor.svelte';
  import ProjectedRow from './ProjectedRow.svelte';
  import ViewItem from './ViewItem.svelte';
  import LiveOutput from './LiveOutput.svelte';
  import { TURN_STOPPED_SENTENCE } from '../../../lib/conversation/transcript';
  let { reader, models, ownSenderLabel, livenessPulse, outgoingMessages = [], acknowledgedMessageIds = new Set<string>(), withdrawnDisplayIds = new Set<string>(), onStopDrawingMessage, onSendAgain }: {
      outgoingMessages?: readonly OutgoingMessage[];
      acknowledgedMessageIds?: ReadonlySet<string>;
      withdrawnDisplayIds?: ReadonlySet<string>;
      onStopDrawingMessage?: (id:string)=>Promise<void>|void;
      onSendAgain?: (id:string)=>Promise<void>|void;
      reader: ConversationViewReader;
      models: readonly BackendModel[];
      ownSenderLabel: string | null;
      livenessPulse: number;
  } = $props();
  let openRows = $derived(reader.openTools);
  let wholeDetails = new SvelteMap<string, string>();
  // Only a deferred ending needs a body read to discover that it has no visual
  // output. Keep the disposition, not the body, and bind it to that exact version.
  let emptyEndings = new SvelteMap<string, string>();
  const items = $derived.by(() => { const map = new Map<string, ConversationViewItem>(); for (const page of reader.pages)
      for (const item of page.items)
          map.set(item.key, item); return [...map.values()].filter(item => { if (reader.lens === 'focus' && (item.kind === 'work_group' || item.kind === 'row' && item.row_kind === 'tool_call')) return false; if (reader.lens === 'full' || item.kind !== 'row' || item.fold_rule !== 'earlier_message' || item.turn_key === null)
      return true; const turn = map.get(item.turn_key); return turn?.kind !== 'turn' || !turn.settled || turn.latest_message_key === item.key || reader.isExpanded({ kind: 'turn', key: turn.key }); }).sort(compareItems); });
  const exits = new MessageExits<MessageDisplaySlot>();
  let exitRevision = $state(0);
  let previousDisplay: MessageDisplaySlot[] = [];
  let ledger: MessageDisplaySlot[] = [];
  let ledgerConversation: string | null = null;
  const displaySlots = $derived.by(() => {
    exitRevision;
    if (ledgerConversation !== reader.id) { ledger = []; previousDisplay = []; ledgerConversation = reader.id; }
    const next = reconcileDisplaySlots(ledger, items, outgoingMessages, acknowledgedMessageIds, withdrawnDisplayIds, reader.coverage);
    // A confirmed queue destination transfers the display slot immediately. It is
    // not a dismissal, so retaining a fading transcript copy would draw it twice.
    const previous = previousDisplay.filter(slot => !withdrawnDisplayIds.has(slot.key));
    const retained = exits.retain(next, previous, () => { exitRevision += 1; });
    ledger = retained.filter(slot => slot.local !== null);
    previousDisplay = retained;
    return retained;
  });
  function hasPresentation(item: ConversationViewItem): boolean {
      if (item.kind === 'turn')
          return !item.settled || !item.stopped || item.tool_call_count > 0 || item.folded_message_count > 0;
      if (item.kind === 'work_group')
          return reader.lens !== 'full' && item.entry_count > 1;
      if (item.row_kind === 'token_usage')
          return false;
      if (item.row_kind !== 'turn_ended')
          return true;
      if (item.body.kind === 'deferred')
          return emptyEndings.get(item.key) !== endingVersion(item.body.reference);
      const body = item.body.value;
      return body.kind !== 'turn_ended' || body.ending !== 'completed' || body.automatic_compaction_result === 'not_compacted';
  }
  function endingVersion(reference: { body_id: string; version: string }): string {
      return JSON.stringify([reference.body_id, reference.version]);
  }
  const presentation = $derived.by(() => {
      const result = new Map<string, { assistantContinuation: boolean; toolContinuation: boolean }>();
      const before = new Set<string>();
      const after = new Set<string>();
      for (const page of reader.pages) {
          if (page.scope.kind === 'timeline')
              continue;
          const first = page.items[0];
          const last = page.items.at(-1);
          if (page.older && first)
              before.add(first.key);
          if (page.newer && last)
              after.add(last.key);
      }
      let previous: ConversationViewItem | undefined;
      for (const item of items) {
          // A held page edge does not establish who is adjacent across the gap.
          if (before.has(item.key))
              previous = undefined;
          if (hasPresentation(item)) {
              result.set(item.key, {
                  assistantContinuation: reader.lens === 'focus' && item.kind === 'row' && item.row_kind === 'agent_message' && previous?.kind === 'row' && previous.row_kind === 'agent_message',
                  toolContinuation: item.kind === 'row' && item.row_kind === 'tool_call' && item.work_group_key !== null && previous?.kind === 'row' && previous.row_kind === 'tool_call' && previous.work_group_key === item.work_group_key
              });
              previous = item;
          }
          if (after.has(item.key))
              previous = undefined;
      }
      return result;
  });
  // Bodies that stopped being true are dropped where they are held, so the rows that
  // read them read again. Nothing is unmounted to achieve it.
  $effect(() => { reader.discardedBodies; untrack(() => { wholeDetails.clear(); emptyEndings.clear(); }); });
  $effect(() => {
      const versions = new Map(items.flatMap(item => item.kind === 'row' && item.row_kind === 'turn_ended' && item.body.kind === 'deferred' ? [[item.key, endingVersion(item.body.reference)] as const] : []));
      for (const [key, version] of emptyEndings)
          if (versions.get(key) !== version)
              emptyEndings.delete(key);
  });
  // Tool outputs are expendable. Logical disclosure keys survive page admission changes.
  $effect(() => { let bytes = 0; for (const [key, value] of [...wholeDetails].reverse()) {
      bytes += value.length * 2;
      if (bytes > 512 * 1024)
          wholeDetails.delete(key);
  } const held = new Set(items.flatMap(i => i.kind === 'row' && i.body.kind === 'inline' && i.body.value.kind === 'tool_call' ? [i.body.value.tool_call_id] : [])); if (openRows.size > 300)
      for (const key of openRows)
          if (!held.has(key))
              openRows.delete(key); });
  function edge(node: HTMLElement, request: {
      scope: ViewHistoryScope;
      cursor: ViewPageCursor;
      direction: 'older' | 'newer';
  }) {
      let current = request;
      const observer = new IntersectionObserver(entries => { if (entries[0]?.isIntersecting && !reader.entryHeld && !['loading','placing','error'].includes(reader.entryPhase))
          void reader.load(current.scope, current.cursor, current.direction); }, { root: node.closest('[data-conversation-thread]'), rootMargin: '50% 0px' });
      observer.observe(node);
      $effect(() => { reader.entryHeld; reader.entryPhase; observer.unobserve(node); observer.observe(node); });
      return { destroy: () => observer.disconnect(), update: (next: typeof request) => { current = next; observer.unobserve(node); observer.observe(node); } };
  }
  /** Ask for this head's own bounded scope, but only once it is near the reader.
   *
   * Every head in the retained window asking at once would be up to a hundred page
   * reads for a thread nobody has scrolled to yet. This is the only thing the
   * transcript ever needed visibility for, so it is the only thing that still has it. */
  function demand(node: HTMLElement, item: ConversationViewItem) {
      if (item.kind !== 'turn' && item.kind !== 'work_group') return { destroy: () => { } };
      const scope: ViewHistoryScope = { kind: item.kind === 'turn' ? 'turn' : 'work_group', key: item.key };
      const ask = (): void => {
          if (reader.entryHeld || ['loading','placing','error'].includes(reader.entryPhase)) return;
          if (reader.lens !== 'full' && !reader.isExpanded(scope)) return;
          if (reader.pages.some(page => page.scope.kind === scope.kind && page.scope.key === scope.key)) return;
          void reader.expand(scope, true);
      };
      const observer = new IntersectionObserver(entries => { if (entries[0]?.isIntersecting) ask(); },
          { root: node.closest('[data-conversation-thread]'), rootMargin: '100% 0px' });
      observer.observe(node);
      $effect(() => { reader.entryHeld; reader.entryPhase; observer.unobserve(node); observer.observe(node); });
      return { destroy: () => observer.disconnect() };
  }
</script>
<div class="c2-transcript" data-conversation-transcript>
  {#if reader.status?.diagnostics_expired}<p class="diagnostic-expiry-notice" data-conversation-diagnostics-expired>Some diagnostic history expired.</p>{/if}
  {#each reader.pages as page (`${page.scope.kind}:${page.scope.key}`)}
    {#if page.scope.kind==='timeline'&&page.older}<div use:edge={{scope:page.scope,cursor:page.older,direction:'older'}} data-conversation-history-edge="older" aria-hidden="true"></div>{/if}
  {/each}
  {#each displaySlots as slot (slot.key)}
    {@const item = slot.item}
    {#if slot.local !== null || item !== null && isMessageRow(item)}
      <ViewItem itemKey={item?.key ?? slot.key} displayKey={slot.key} registerDisplayNode={exits.register}>
        <ProjectedMessage item={item?.kind === 'row' ? item : null} local={slot.local} {reader} {ownSenderLabel} {onStopDrawingMessage} {onSendAgain}/>
      </ViewItem>
    {:else if item !== null}
    {#each reader.pages.filter(page=>page.scope.kind!=='timeline'&&page.items[0]?.key===item.key&&page.older) as page (`older:${page.scope.kind}:${page.scope.key}`)}
      {#if page.older}<div use:edge={{scope:page.scope,cursor:page.older,direction:'older'}} data-conversation-history-edge="older" aria-hidden="true"></div>{/if}
    {/each}
    {#if item.kind!=='row'||presentation.has(item.key)}
    <ViewItem itemKey={item.key} marker={!presentation.has(item.key)} assistantContinuation={presentation.get(item.key)?.assistantContinuation??false} tool={item.kind==='row'&&item.row_kind==='tool_call'} toolContinuation={presentation.get(item.key)?.toolContinuation??false}>
      {#if item.kind==='turn'}
        <div use:demand={item}>
          <TurnAnchor settled={item.settled} stopped={item.stopped} startedAtUnixMilliseconds={item.started_at_unix_milliseconds} ending={item.ending} isLatest={item.is_latest} durationSeconds={item.duration_seconds} toolCallCount={item.tool_call_count} foldedMessageCount={item.folded_message_count} expanded={reader.lens==='full'||reader.isExpanded({kind:'turn',key:item.key})} {livenessPulse} onToggle={reader.lens==='full'?undefined:()=>void reader.expand({kind:'turn',key:item.key},!reader.isExpanded({kind:'turn',key:item.key}))}/>
        </div>
      {:else if item.kind==='work_group'}
        <div use:demand={item} data-conversation-work-group>
          {#if reader.lens!=='full'&&item.entry_count>1}<button type="button" class="c2-run-toggle" data-conversation-work-fold onclick={()=>void reader.expand({kind:'work_group',key:item.key},!reader.isExpanded({kind:'work_group',key:item.key}))}>{item.entry_count-1} earlier tool calls</button>{/if}
        </div>
      {:else}
        <ProjectedRow {item} {reader} {models} {ownSenderLabel} {openRows} {wholeDetails} onEmptyEnding={(reference)=>emptyEndings.set(item.key,endingVersion(reference))}/>
      {/if}
    </ViewItem>
    {/if}
    {#each reader.pages.filter(page=>page.scope.kind!=='timeline'&&page.items.at(-1)?.key===item.key&&page.newer) as page (`newer:${page.scope.kind}:${page.scope.key}`)}
      {#if page.newer}<div use:edge={{scope:page.scope,cursor:page.newer,direction:'newer'}} data-conversation-history-edge="newer" aria-hidden="true"></div>{/if}
    {/each}
    {/if}
  {/each}
  {#if reader.lens==='full'&&reader.status?.live_output}
    <ViewItem itemKey="live-output" marker={reader.status.live_output.byte_count===0}><LiveOutput {reader}/></ViewItem>
  {/if}
  {#if reader.status?.stopped_without_ending}<div class="acp-turn-end" data-conversation-row="turn_stopped">{TURN_STOPPED_SENTENCE}</div>{/if}
  {#each reader.pages as page (`${page.scope.kind}:${page.scope.key}`)}
    {#if page.scope.kind==='timeline'&&page.newer}<div use:edge={{scope:page.scope,cursor:page.newer,direction:'newer'}} data-conversation-history-edge="newer" aria-hidden="true"></div>{/if}
  {/each}
</div>
<style>.diagnostic-expiry-notice{color:var(--text-muted);font-size:var(--type-xs);margin:0}.c2-transcript{display:contents}[data-conversation-history-edge]{height:1px;flex:none;margin-block-start:calc(-1px - var(--space-5))}</style>
