<script lang="ts">
  /** v8: the live edge. While a turn runs, its status ("Working · 2m 14s" with the spinner)
   *  comes right after the running turn's content (its steered messages included) and before
   *  the queued messages, which send after this turn. v9 (1): this line carries the spinner in every view. The turn's top shows nothing while it is live; "Worked for" takes the top
   *  once it ends. v9 (24, owner): no narration sentence under the line, in any view. */
  import TurnAnchor from '../TurnAnchor.svelte';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { liveTurnOf } from '../../../lib/conversation/view/liveEdge';

  let { reader, activityUncertain = false, livenessPulse = 0 }: {
    reader: ConversationViewReader;
    activityUncertain?: boolean;
    livenessPulse?: number;
  } = $props();

  const turn = $derived(liveTurnOf(reader.pages, reader.status));
  const waiting = $derived(reader.status?.pending_permission ? 'permission' as const : reader.status?.pending_user_input ? 'question' as const : null);
  const shown = $derived(turn !== null && (waiting === null || activityUncertain));
</script>

{#if shown && turn !== null}
  <div class="c2-live-edge" data-conversation-live-edge>
    <TurnAnchor {activityUncertain} settled={false} stopped={false} startedAtUnixMilliseconds={turn.started_at_unix_milliseconds} isLatest={true} {waiting} {livenessPulse}/>
  </div>
{/if}

<style>
  /* It ends the running turn's content, so it stands a block's gap under it. */
  .c2-live-edge {
    flex: none;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    min-width: 0;
    margin-block-start: var(--cv-gap-block);
  }
</style>
