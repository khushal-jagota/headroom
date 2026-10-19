<script lang="ts">
  import { SvelteMap, SvelteSet } from 'svelte/reactivity';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { compareItems, DOM_ITEMS } from '../../../lib/conversation/view/reader.svelte';
  import type { ConversationViewItem, ViewHistoryScope, ViewPageCursor } from '../../../lib/conversation/view/contracts';
  import type { BackendModel } from '../../../lib/conversation/wire';
  import TurnAnchor from '../TurnAnchor.svelte';
  import ProjectedRow from './ProjectedRow.svelte';
  import ViewItem from './ViewItem.svelte';
  import LiveOutput from './LiveOutput.svelte';
  import { TURN_STOPPED_SENTENCE } from '../../../lib/conversation/transcript';
  let { reader, models, ownSenderLabel, livenessPulse }: {
      reader: ConversationViewReader;
      models: readonly BackendModel[];
      ownSenderLabel: string | null;
      livenessPulse: number;
  } = $props();
  let visible = new SvelteSet<string>();
  let openRows = $derived(reader.openTools);
  let wholeDetails = new SvelteMap<string, string>();
  const items = $derived.by(() => { const map = new Map<string, ConversationViewItem>(); for (const page of reader.pages)
      for (const item of page.items)
          map.set(item.key, item); return [...map.values()].filter(item => { if (reader.lens === 'full' || item.kind !== 'row' || item.row_kind !== 'agent_message' || item.turn_key === null)
      return true; const turn = map.get(item.turn_key); return turn?.kind !== 'turn' || !turn.settled || turn.latest_message_key === item.key || reader.isExpanded({ kind: 'turn', key: turn.key }); }).sort(compareItems); });
  const mounted = $derived(new Set(items.filter(item => visible.has(item.key)).slice(-(DOM_ITEMS - 1)).map(item => item.key)));
  function onVisible(key: string, value: boolean): void { if (value)
      visible.add(key);
  else
      visible.delete(key); }
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
      const observer = new IntersectionObserver(entries => { if (entries[0]?.isIntersecting)
          void reader.load(current.scope, current.cursor, current.direction); }, { root: node.closest('[data-conversation-thread]'), rootMargin: '50% 0px' });
      observer.observe(node);
      return { destroy: () => observer.disconnect(), update: (next: typeof request) => { current = next; observer.unobserve(node); observer.observe(node); } };
  }
  function demand(node: HTMLElement, item: ConversationViewItem) {
      if ((item.kind === 'turn' || item.kind === 'work_group')) {
          const scope: ViewHistoryScope = { kind: item.kind === 'turn' ? 'turn' : 'work_group', key: item.key };
          if (reader.lens === 'full' || reader.isExpanded(scope)) {
              if (!reader.pages.some(page => page.scope.kind === scope.kind && page.scope.key === scope.key))
                  void reader.expand(scope, true);
          }
      }
      return { destroy: () => { } };
  }
</script>
<div class="c2-transcript" data-conversation-transcript>
  {#each reader.pages as page (`${page.scope.kind}:${page.scope.key}`)}
    {#if page.scope.kind==='timeline'&&page.older}<div use:edge={{scope:page.scope,cursor:page.older,direction:'older'}} data-conversation-history-edge="older" aria-hidden="true"></div>{/if}
  {/each}
  {#each items as item (item.key)}
    {#each reader.pages.filter(page=>page.scope.kind!=='timeline'&&page.items[0]?.key===item.key&&page.older) as page (`older:${page.scope.kind}:${page.scope.key}`)}
      {#if page.older}<div use:edge={{scope:page.scope,cursor:page.older,direction:'older'}} data-conversation-history-edge="older" aria-hidden="true"></div>{/if}
    {/each}
    <ViewItem itemKey={item.key} active={mounted.has(item.key)} {onVisible}>
      {#if item.kind==='turn'}
        <div use:demand={item}>
          <TurnAnchor settled={item.settled} stopped={item.stopped} startedAtUnixMilliseconds={item.started_at_unix_milliseconds} ending={item.ending} isLatest={item.is_latest} durationSeconds={item.duration_seconds} toolCallCount={item.tool_call_count} foldedMessageCount={item.folded_message_count} expanded={reader.lens==='full'||reader.isExpanded({kind:'turn',key:item.key})} {livenessPulse} onToggle={reader.lens==='full'?undefined:()=>void reader.expand({kind:'turn',key:item.key},!reader.isExpanded({kind:'turn',key:item.key}))}/>
        </div>
      {:else if item.kind==='work_group'}
        <div use:demand={item} data-conversation-work-group>
          {#if reader.lens!=='full'&&item.entry_count>1}<button type="button" class="c2-run-toggle" data-conversation-work-fold onclick={()=>void reader.expand({kind:'work_group',key:item.key},!reader.isExpanded({kind:'work_group',key:item.key}))}>{item.entry_count-1} earlier tool calls</button>{/if}
        </div>
      {:else}
        <ProjectedRow {item} {reader} {models} {ownSenderLabel} {openRows} {wholeDetails}/>
      {/if}
    </ViewItem>
    {#each reader.pages.filter(page=>page.scope.kind!=='timeline'&&page.items.at(-1)?.key===item.key&&page.newer) as page (`newer:${page.scope.kind}:${page.scope.key}`)}
      {#if page.newer}<div use:edge={{scope:page.scope,cursor:page.newer,direction:'newer'}} data-conversation-history-edge="newer" aria-hidden="true"></div>{/if}
    {/each}
  {/each}
  {#if reader.lens==='full'&&reader.status?.live_output}
    <ViewItem itemKey="live-output" active={visible.has('live-output')} {onVisible}><LiveOutput {reader}/></ViewItem>
  {/if}
  {#if reader.status?.stopped_without_ending}<div class="acp-turn-end" data-conversation-row="turn_stopped">{TURN_STOPPED_SENTENCE}</div>{/if}
  {#each reader.pages as page (`${page.scope.kind}:${page.scope.key}`)}
    {#if page.scope.kind==='timeline'&&page.newer}<div use:edge={{scope:page.scope,cursor:page.newer,direction:'newer'}} data-conversation-history-edge="newer" aria-hidden="true"></div>{/if}
  {/each}
</div>
<style>.c2-transcript{display:contents}[data-conversation-history-edge]{height:1px;flex:none}</style>
