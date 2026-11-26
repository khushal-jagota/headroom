<script lang="ts">
  import { MessageExits } from '../../../lib/conversation/messageExits';
  import { reconcileDisplaySlots, isMessageRow, type MessageDisplaySlot } from '../../../lib/conversation/displaySlots';
  import type { OutgoingMessage } from '../../../lib/conversation/outgoing';
  import ProjectedMessage from './ProjectedMessage.svelte';
  import { transcriptHistoryEdges, rowForBody } from '../../../lib/conversation/view/presentation';
  import { summarizeToolRun } from '../../../lib/conversation/toolCallPresentation';
  import type { ToolRunCounts } from '../../../lib/conversation/toolRunKind';
  import type { ToolCallRow as ToolCallRowType } from '../../../lib/conversation/transcript';
  import ToolRunSummary from '../ToolRunSummary.svelte';
  import { getContext, untrack } from 'svelte';
  import { SvelteMap, SvelteSet } from 'svelte/reactivity';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { compareItems } from '../../../lib/conversation/view/reader.svelte';
  import type { ConversationViewItem, ViewHistoryScope, ViewPageCursor } from '../../../lib/conversation/view/contracts';
  import type { BackendModel } from '../../../lib/conversation/wire';
  import TurnAnchor from '../TurnAnchor.svelte';
  import ProjectedRow from './ProjectedRow.svelte';
  import ViewItem from './ViewItem.svelte';
  import LiveOutput from './LiveOutput.svelte';
  import TranscriptStatusRow from '../TranscriptStatusRow.svelte';
  import { DOCKED_CREDENTIAL_REQUEST } from '../../../lib/conversation/composer';
  import { TURN_STOPPED_STATUS, ASK_CARD_CONTEXT, askShownByItsCard, backgroundTasksAfterTheTurn, backgroundWorkSentence, type AskCardReading } from '../../../lib/conversation/transcript';
  let { activityUncertain = false,
    destinationName, reader, models, ownSenderLabel, outgoingMessages = [], acknowledgedMessageIds = new Set<string>(), withdrawnDisplayIds = new Set<string>(), onStopDrawingMessage, onSendAgain }: {
      outgoingMessages?: readonly OutgoingMessage[];
      acknowledgedMessageIds?: ReadonlySet<string>;
      withdrawnDisplayIds?: ReadonlySet<string>;
      onStopDrawingMessage?: (id:string)=>Promise<void>|void;
      onSendAgain?: (id:string)=>Promise<void>|void;
      activityUncertain?: boolean;
    destinationName?: string;
      reader: ConversationViewReader;
      models: readonly BackendModel[];
      ownSenderLabel: string | null;
  } = $props();
  const askCard = getContext<AskCardReading | undefined>(ASK_CARD_CONTEXT);
  /** The key request the composer is asking in its own place; drawn there, not here. */
  const dockedCredential = getContext<(() => string | null) | undefined>(DOCKED_CREDENTIAL_REQUEST);
  let openRows = $derived(reader.openTools);
  let wholeDetails = new SvelteMap<string, string>();
  // Only a deferred ending needs a body read to discover that it has no visual
  // output. Keep the disposition, not the body, and bind it to that exact version.
  let emptyEndings = new SvelteMap<string, string>();
  // Which runs of tool calls the reader opened in Full. Every run starts closed.
  let openRuns = new SvelteSet<string>();
  const everyItem = $derived.by(() => { const map = new Map<string, ConversationViewItem>(); for (const page of reader.pages)
      for (const item of page.items)
          map.set(item.key, item); return map; });
  /** A run of more than one call stands behind its summary; a run of one is its own row. */
  function summarized(group: ConversationViewItem | undefined): boolean {
      return group?.kind === 'work_group' && group.entry_count > 1;
  }
  const items = $derived.by(() => { const map = everyItem; return [...map.values()].filter(item => {
      const isWork = item.kind === 'work_group' || item.kind === 'row' && item.row_kind === 'tool_call';
      // Focus is focus: no tool call, no run and no count of them is drawn there.
      if (reader.lens === 'focus' && isWork) return false;
      if (reader.lens === 'full' && item.kind === 'row' && item.row_kind === 'tool_call' && item.work_group_key !== null && summarized(map.get(item.work_group_key)) && !openRuns.has(item.work_group_key)) return false;
      if (reader.lens === 'full' || item.kind !== 'row' || item.fold_rule !== 'earlier_message' || item.turn_key === null)
      return true; const turn = map.get(item.turn_key); return turn?.kind !== 'turn' || !turn.settled || turn.latest_message_key === item.key || reader.isExpanded({ kind: 'turn', key: turn.key }); }).sort(compareItems); });
  /** What the server counted of a closed run's calls, for its line before they are at hand. */
  function countedRun(group: ConversationViewItem & { kind: 'work_group' }): ToolRunCounts | null {
      const counted = group.call_summary;
      return counted === undefined ? null : { kinds: counted.kinds as ToolRunCounts['kinds'], failedCount: counted.failed_count };
  }
  /** Open or close a run. Opening it is what asks for its calls, once. */
  function toggleRun(groupKey: string): void {
      if (openRuns.has(groupKey)) { openRuns.delete(groupKey); return; }
      openRuns.add(groupKey);
      if (!reader.pages.some(page => page.scope.kind === 'work_group' && page.scope.key === groupKey))
          void reader.expand({ kind: 'work_group', key: groupKey }, true);
  }
  /** Each run's calls that are at hand, as rows, for its summary line. */
  const runRows = $derived.by(() => {
      const rows = new Map<string, ToolCallRowType[]>();
      for (const item of [...everyItem.values()].sort(compareItems)) {
          if (item.kind !== 'row' || item.row_kind !== 'tool_call' || item.work_group_key === null || item.body.kind !== 'inline') continue;
          const row = rowForBody(item, item.body.value);
          if (row.kind !== 'tool_call') continue;
          rows.set(item.work_group_key, [...(rows.get(item.work_group_key) ?? []), row]);
      }
      return rows;
  });
  const historyEdges = $derived(transcriptHistoryEdges(reader.pages, items));
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
  /** Turn timing has one place, the top of the turn. A running turn's head ("Working for
   *  2m 14s" with its spinner) stands where "Worked for 7m" stands on a finished one, a pending
   *  ask included; nothing counts at the tail of the transcript. */
  const drawnSlots = $derived(displaySlots);
  /** A turn the person stopped ends with its one line ("Stopped after 8m 45s"). Focus shows
   *  only that line; Full also heads the turn with "Worked for …", as it does every turn. */
  function headDrawn(item: ConversationViewItem & { kind: 'turn' }): boolean {
      return reader.lens === 'full' || !(item.settled && item.ending === 'interrupted');
  }
  function hasPresentation(item: ConversationViewItem): boolean {
      if (item.kind === 'turn')
          return headDrawn(item)
              && (!item.settled || !item.stopped || item.tool_call_count > 0 || item.folded_message_count > 0);
      // In Full a run of more than one call is drawn as its summary line.
      if (item.kind === 'work_group')
          return reader.lens === 'full' && item.entry_count > 1;
      if (item.row_kind === 'token_usage')
          return false;
      if (item.body.kind === 'inline' && item.body.value.kind === 'permission_ask')
          return !askShownByItsCard({ state: item.body.value.state, answerFate: item.body.value.answer_fate }, activityUncertain, askCard?.saysAvailabilityUnknown('permission', item.body.value.ask_id) ?? false);
      if (item.body.kind === 'inline' && item.body.value.kind === 'user_input')
          return !askShownByItsCard({ state: item.body.value.state, answerFate: item.body.value.answer_fate }, activityUncertain, askCard?.saysAvailabilityUnknown('user_input', item.body.value.request_id) ?? false);
      if (item.body.kind === 'inline' && item.body.value.kind === 'credential_request')
          return item.body.value.request_id !== (dockedCredential?.() ?? null);
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
  /** Where each drawn item stands in the thread's rhythm (design direction 2.5), from what
   *  it is and what was drawn before it. Items that draw nothing take no space. */
  type Rhythm = 'first' | 'turn' | 'break' | 'meta' | 'block' | 'row' | 'none';
  type Beat = 'prompt' | 'head' | 'work' | 'prose' | 'other';
  const presentation = $derived.by(() => {
      const result = new Set<string>();
      for (const item of items)
          if (hasPresentation(item))
              result.add(item.key);
      return result;
  });
  const layout = $derived.by(() => {
      const result = new Map<string, Rhythm>();
      const before = new Set<string>();
      for (const boundary of historyEdges)
          if (boundary.anchorKey !== null && boundary.direction === 'older')
              before.add(boundary.anchorKey);
      let previous: Beat | null = null;
      // A turn whose head draws nothing still starts a turn: what comes first in it takes
      // the turn gap.
      let turnOpens = false;
      for (const slot of drawnSlots) {
          const item = slot.item;
          // A held page edge does not establish who is adjacent across the gap.
          if (item !== null && before.has(item.key)) previous = null;
          if (item !== null && item.kind === 'turn' && !presentation.has(item.key)) { turnOpens = true; result.set(slot.key, 'none'); continue; }
          let beat: Beat | null;
          if (slot.local !== null || item !== null && (isMessageRow(item) || item.kind === 'row' && item.row_kind === 'agent_turn_started')) beat = 'prompt';
          else if (item === null || !presentation.has(item.key)) beat = null;
          else if (item.kind === 'turn') beat = 'head';
          else if (item.kind === 'work_group' || item.row_kind === 'tool_call') beat = 'work';
          else if (item.row_kind === 'agent_message') beat = 'prose';
          else beat = 'other';
          if (beat === null) { result.set(slot.key, 'none'); continue; }
          const opens = turnOpens; turnOpens = false;
          // An agent message, or an agent-started turn, straight after an agent message (no
          // prompt or turn break between them) is set off by a short break, not a bare gap.
          const agentStarted = item !== null && item.kind === 'row' && item.row_kind === 'agent_turn_started';
          result.set(slot.key,
              previous === null ? 'first'
              : previous === 'prose' && (beat === 'prose' || agentStarted) ? 'break'
              // A message keeps its hover row's room under it (4 + 24, and 4 clear): what
              // follows it stands a turn's gap below, whatever it is.
              : beat === 'prompt' || beat === 'head' || opens || messageBeat(previous) ? 'turn'
              : previous === 'head' ? 'meta'
              : beat === 'work' && previous === 'work' ? 'row'
              : 'block');
          previous = beat;
      }
      return { rhythm: result, endsWithMessage: messageBeat(previous) };
  });
  const rhythm = $derived(layout.rhythm);
  /** A message (the person's or the agent's) carries the time-and-Copy row under it. */
  function messageBeat(beat: Beat | null): boolean {
      return beat === 'prompt' || beat === 'prose';
  }
  /** A line drawn after the last item (live output, the stopped line, the stopped line) keeps
   *  the turn gap under a message too. */
  const endsWithMessage = $derived(layout.endsWithMessage);
  const liveOutputDrawn = $derived(reader.lens === 'full' && (reader.status?.live_output?.byte_count ?? 0) > 0);
  const stoppedLineDrawn = $derived(reader.status?.stopped_without_ending === true);
  const backgroundTasks = $derived(activityUncertain ? [] : backgroundTasksAfterTheTurn(reader.status));
  /** The first line drawn after the items, if any, takes the turn gap under a message. */
  const trailing = $derived(liveOutputDrawn ? 'live-output' : stoppedLineDrawn ? 'stopped' : backgroundTasks.length > 0 ? 'background' : null);
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
      const observer = new IntersectionObserver(entries => { if (entries[0]?.isIntersecting && !['loading','placing','error'].includes(reader.entryPhase))
          void reader.load(current.scope, current.cursor, current.direction); }, { root: node.closest('[data-conversation-thread]'), rootMargin: '50% 0px' });
      observer.observe(node);
      $effect(() => { reader.entryPhase; observer.unobserve(node); observer.observe(node); });
      return { destroy: () => observer.disconnect(), update: (next: typeof request) => { current = next; observer.unobserve(node); observer.observe(node); } };
  }
  /** Ask for this Focus head's own bounded scope, but only once it is near the reader.
   *
   * Full asks for nothing here: its timeline already holds every turn's messages and runs,
   * and a run's calls load only when the person opens it.
   *
   * Every head in the retained window asking at once would be up to a hundred page
   * reads for a thread nobody has scrolled to yet. This is the only thing the
   * transcript ever needed visibility for, so it is the only thing that still has it. */
  function demand(node: HTMLElement, item: ConversationViewItem) {
      if (item.kind !== 'turn' && item.kind !== 'work_group') return { destroy: () => { } };
      const scope: ViewHistoryScope = { kind: item.kind === 'turn' ? 'turn' : 'work_group', key: item.key };
      const ask = (): void => {
          if (reader.lens === 'full' || ['loading','placing','error'].includes(reader.entryPhase)) return;
          // Focus reaches a run through its opened turn rather than one run at a time.
          const opened = reader.isExpanded(scope) || (item.kind === 'work_group' && item.turn_key !== null && reader.isExpanded({ kind: 'turn', key: item.turn_key }));
          if (!opened) return;
          if (reader.pages.some(page => page.scope.kind === scope.kind && page.scope.key === scope.key)) return;
          void reader.expand(scope, true);
      };
      const observer = new IntersectionObserver(entries => { if (entries[0]?.isIntersecting) ask(); },
          { root: node.closest('[data-conversation-thread]'), rootMargin: '100% 0px' });
      observer.observe(node);
      $effect(() => { reader.entryPhase; observer.unobserve(node); observer.observe(node); });
      return { destroy: () => observer.disconnect() };
  }
</script>
<div class="c2-transcript" data-conversation-transcript>
  {#if reader.status?.diagnostics_expired}<p class="diagnostic-expiry-notice" data-conversation-diagnostics-expired>Some diagnostic history expired.</p>{/if}
  {#each reader.pages as page (`${page.scope.kind}:${page.scope.key}`)}
    {#if page.scope.kind==='timeline'&&page.older}<div use:edge={{scope:page.scope,cursor:page.older,direction:'older'}} data-conversation-history-edge="older" aria-hidden="true"></div>{/if}
  {/each}
  {#each drawnSlots as slot (slot.key)}
    {@const item = slot.item}
    {#each historyEdges.filter(boundary => boundary.direction === 'older' && boundary.anchorKey === item?.key) as boundary (`older:${boundary.scope.kind}:${boundary.scope.key}`)}
      <div use:edge={boundary} data-conversation-history-edge="older" aria-hidden="true"></div>
    {/each}
    {#if slot.local !== null || item !== null && isMessageRow(item)}
      <ViewItem itemKey={item?.key ?? slot.key} displayKey={slot.key} registerDisplayNode={exits.register} rhythm={rhythm.get(slot.key) ?? 'block'}>
        <ProjectedMessage item={item?.kind === 'row' ? item : null} local={slot.local} {reader} {ownSenderLabel} {onStopDrawingMessage} {onSendAgain}/>
      </ViewItem>
    {:else if item !== null}
    {#if item.kind!=='row'||presentation.has(item.key)}
    <ViewItem itemKey={item.key} rhythm={rhythm.get(slot.key) ?? 'block'}>
      {#if item.kind==='turn'}
        <div use:demand={item}>
          {#if headDrawn(item)}
          <TurnAnchor {activityUncertain} {destinationName} settled={item.settled} stopped={item.stopped} startedAtUnixMilliseconds={item.started_at_unix_milliseconds} durationSeconds={item.duration_seconds} toolCallCount={item.tool_call_count} foldedMessageCount={item.folded_message_count} expanded={reader.lens==='full'||reader.isExpanded({kind:'turn',key:item.key})} onToggle={reader.lens==='full'?undefined:()=>void reader.expand({kind:'turn',key:item.key},!reader.isExpanded({kind:'turn',key:item.key}))}/>
          {/if}
        </div>
      {:else if item.kind==='work_group'}
        {@const groupKey = item.key}
        <div use:demand={item} data-conversation-work-group data-conversation-work-expanded={openRuns.has(groupKey) ? 'true' : 'false'}>
          {#if presentation.has(groupKey)}
            <ToolRunSummary summary={summarizeToolRun(runRows.get(groupKey) ?? [], item.entry_count, countedRun(item))} callCount={item.entry_count} open={openRuns.has(groupKey)} {activityUncertain} onToggle={() => toggleRun(groupKey)}/>
          {/if}
        </div>
      {:else}
        <ProjectedRow {activityUncertain} {destinationName} {item} {reader} {models} {ownSenderLabel} {openRows} {wholeDetails} onEmptyEnding={(reference)=>emptyEndings.set(item.key,endingVersion(reference))}/>
      {/if}
    </ViewItem>
    {/if}
    {/if}
    {#each historyEdges.filter(boundary => boundary.direction === 'newer' && boundary.anchorKey === item?.key) as boundary (`newer:${boundary.scope.kind}:${boundary.scope.key}`)}
      <div use:edge={boundary} data-conversation-history-edge="newer" aria-hidden="true"></div>
    {/each}
  {/each}
  {#each historyEdges.filter(boundary => boundary.anchorKey === null) as boundary (`${boundary.direction}:${boundary.scope.kind}:${boundary.scope.key}`)}
    <div use:edge={boundary} data-conversation-history-edge={boundary.direction} aria-hidden="true"></div>
  {/each}
  {#if reader.lens==='full'&&reader.status?.live_output}
    <ViewItem itemKey="live-output" rhythm={reader.status.live_output.byte_count===0 ? 'none' : endsWithMessage ? 'turn' : 'block'}><LiveOutput {reader}/></ViewItem>
  {/if}
  {#if stoppedLineDrawn}<TranscriptStatusRow status={TURN_STOPPED_STATUS} role="status" data-conversation-row="turn_stopped" data-rhythm={endsWithMessage && trailing === 'stopped' ? 'turn' : undefined}/>{/if}
  <!-- The turn has ended but work it started is still live: say so, one line per task,
       from the record the sidebar spinner reads, until that task ends. -->
  {#each backgroundTasks as task, index (task.task_id)}
      <div class="c2-background-work" role="status" data-conversation-background-task data-rhythm={index === 0 && endsWithMessage && trailing === 'background' ? 'turn' : undefined}>
        <span class="live-text-shimmer">{backgroundWorkSentence([task.task_description])}</span>
      </div>
  {/each}
  {#each reader.pages as page (`${page.scope.kind}:${page.scope.key}`)}
    {#if page.scope.kind==='timeline'&&page.newer}<div use:edge={{scope:page.scope,cursor:page.newer,direction:'newer'}} data-conversation-history-edge="newer" aria-hidden="true"></div>{/if}
  {/each}
</div>
<style>.c2-background-work{align-self:flex-start;color:var(--cv-t3);font-family:var(--cv-font);font-size:var(--cv-type-meta);line-height:var(--cv-leading-meta)}.diagnostic-expiry-notice{color:var(--cv-t3);font-family:var(--cv-font);font-size:var(--cv-type-caption);line-height:var(--cv-leading-caption);margin:0}.c2-transcript{display:contents}[data-conversation-history-edge]{height:1px;flex:none;margin-block:-1px 0}</style>
