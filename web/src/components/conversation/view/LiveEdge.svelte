<script lang="ts">
  /** v8: the live edge. While a turn runs, its status ("Working · 2m 14s" with the spinner)
   *  comes right after the running turn's content (its steered messages included) and before
   *  the queued messages, which send after this turn. In Full, while a tool call runs, that
   *  row carries the spinner and this line a still dot. The turn's top shows nothing while it is live; "Worked for" takes the top
   *  once it ends. In Focus, the agent's latest narration sentence stands under the line:
   *  its own prose, one line, never a tool or a command. */
  import TurnAnchor from '../TurnAnchor.svelte';
  import type { ConversationViewReader } from '../../../lib/conversation/view/reader.svelte';
  import { liveTurnOf, liveTurnRunsATool } from '../../../lib/conversation/view/liveEdge';

  let { reader, activityUncertain = false, livenessPulse = 0 }: {
    reader: ConversationViewReader;
    activityUncertain?: boolean;
    livenessPulse?: number;
  } = $props();

  const turn = $derived(liveTurnOf(reader.pages, reader.status));
  const waiting = $derived(reader.status?.pending_permission ? 'permission' as const : reader.status?.pending_user_input ? 'question' as const : null);
  const shown = $derived(turn !== null && (waiting === null || activityUncertain));
  // v8 (5): the narration sentence shows in Focus only; in Full it is already on screen as prose.
  const narration = $derived(reader.lens === 'focus' && !activityUncertain ? (reader.status?.live_narration ?? null) : null);
  const mark = $derived(reader.lens === 'full' && turn !== null && liveTurnRunsATool(reader.pages, turn.key) ? 'dot' as const : 'spinner' as const);
</script>

{#if shown && turn !== null}
  <div class="c2-live-edge" data-conversation-live-edge>
    <TurnAnchor {activityUncertain} settled={false} stopped={false} startedAtUnixMilliseconds={turn.started_at_unix_milliseconds} isLatest={true} {waiting} {livenessPulse} liveMark={mark}/>
    {#if narration}
      <p class="c2-live-narration" data-conversation-live-narration title={narration}>{narration}</p>
    {/if}
  </div>
{/if}

<style>
  /* v8 (3): it ends the running turn's content, so it stands 24px under it (a block gap
     inside the turn), not a turn's 40. */
  .c2-live-edge {
    flex: none;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    min-width: 0;
    margin-block-start: 24px;
  }
  /* v6: 14/20 #b0b0b0, one line, ellipsis at the column edge. */
  .c2-live-narration {
    margin: 0 0 0 24px;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    color: #b0b0b0;
    font: var(--cv-weight-body) 14px/20px var(--cv-font);
  }
</style>
