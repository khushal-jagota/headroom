<script lang="ts">
  /** The dock keeps activity separate from unread owner content.
   * Requests and delivery failures retain their own feedback below the status.
   * Only the arrow opens full conversation.
   */
  import {
    elapsedSecondsSince,
    formatDuration,
    millisecondsUntilNextSecond
  } from "../../lib/conversation/transcript";
  import type { RestLine } from "../../lib/conversation/restLine";

  let { line, preview = null, roleLabel = "Worker", activityKnown = true, connectionTrouble = false,
    running = false, feedback = null, onOpen }: { line: RestLine | null; preview?: string | null;
    roleLabel?: "Worker" | "Manager" | "Chief"; activityKnown?: boolean; connectionTrouble?: boolean;
    running?: boolean; feedback?: string | null; onOpen: () => void } = $props();
  let activity = $derived(running ? "Working" : activityKnown ? `${roleLabel} idle` : null);

  /** The one thing on this line that changes on its own. Nothing else is touched by a
   *  tick, and the line is redrawn from the record as the record changes. */
  let elapsedSeconds = $state<number | null>(null);
  // Held apart from the line itself so a record that changed under a running turn does
  // not restart the counting.
  let workingSince = $derived(line?.workingSinceUnixMilliseconds ?? null);

  $effect(() => {
    const begun = workingSince;
    if (begun === null) {
      elapsedSeconds = null;
      return;
    }
    let waiting: ReturnType<typeof setTimeout> | undefined;
    // Each tick is aimed at the next whole second since the turn began, and the number is
    // worked out from that instant every time — so nothing drifts and nothing accumulates.
    const tick = (): void => {
      const now = Date.now();
      elapsedSeconds = elapsedSecondsSince(begun, now);
      waiting = setTimeout(tick, millisecondsUntilNextSecond(begun, now));
    };
    tick();
    return () => clearTimeout(waiting);
  });
</script>

<div class="c2-rest" data-conversation-rest-bar>
  <div class="c2-rest-status">
    {#if running}<span class="c2-mark--running" aria-label="working"></span>{/if}
    {#if connectionTrouble}<span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>{/if}
    {#if activity}<span data-conversation-rest-line>{activity}</span>{/if}
    {#if elapsedSeconds !== null}<span class="c2-rest-aside">{formatDuration(elapsedSeconds)}</span>{/if}
    <button type="button" class="chat-overflow-btn" data-conversation-expand data-conversation-state-control
      aria-label="Open the conversation full height" title="Open the conversation full height" onclick={onOpen}>
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 14 6-6 6 6" /></svg>
    </button>
  </div>
  {#if feedback}<div class="c2-rest-feedback" role="status" data-conversation-dock-feedback>{feedback}</div>{/if}
  {#if preview}<div class="c2-rest-preview" data-conversation-unread-preview>{preview}</div>{/if}
</div>

<style>
  .c2-rest { flex:none; min-width:0; padding-block:var(--space-2); color:var(--text-faint); font:var(--type-xs)/1.5 var(--font-mono); }
  .c2-rest-status { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
  .c2-rest-status [data-conversation-rest-line] { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
  .c2-rest-status button { margin-left:auto; flex:none; }
  .c2-rest-aside { font-variant-numeric:tabular-nums; }
  .c2-rest-preview { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; line-clamp:3; overflow:hidden;
    white-space:pre-line; color:var(--text-strong); font:var(--type-sm)/1.5 var(--font-ui); margin-bottom:var(--space-2); }
  .c2-mark--running { flex:none; width:11px; height:11px; border-radius:50%; border:1.5px solid var(--text-faintest);
    border-top-color:var(--text-muted); animation:spin 900ms linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion:reduce) { .c2-mark--running { animation:none; } }
</style>
