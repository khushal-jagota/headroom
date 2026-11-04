<script lang="ts">
  /** Activity and the unread preview belong to the conversation surface.
   * Delivery and request feedback remains independent of the control that opens it. */
  let { preview = null, roleLabel = "Worker", activityKnown = true, connectionTrouble = false,
    running = false, feedback = null, onOpen }: { preview?: string | null;
    roleLabel?: "Worker" | "Manager" | "Chief" | "Goal"; activityKnown?: boolean; connectionTrouble?: boolean;
    running?: boolean; feedback?: string | null; onOpen: () => void } = $props();
  let activity = $derived(running ? "Working" : activityKnown ? `${roleLabel} idle` : null);
</script>

<button type="button" class="c2-rest" data-conversation-rest-bar data-conversation-expand data-conversation-state-control
  aria-label="Open the conversation full height" title="Open the conversation full height" onclick={onOpen}>
  <span class="c2-rest-status">
    <span class="c2-rest-activity">
      {#if running}<span class="c2-mark--running" aria-label="working"></span>{/if}
      {#if connectionTrouble}<span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>{/if}
      {#if activity}<span data-conversation-rest-line>{activity}</span>{/if}
    </span>
    <span class="c2-rest-control" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M12 19V5M5 12l7-7 7 7" /></svg>
    </span>
  </span>
  {#if preview}<span class="c2-rest-preview" data-conversation-unread-preview>{preview}</span>{/if}
</button>
{#if feedback}<div class="c2-rest-feedback" role="status" data-conversation-dock-feedback>{feedback}</div>{/if}

<style>
  .c2-rest { display:flex; flex:none; flex-direction:column; align-items:stretch; gap:var(--space-1);
    width:100%; min-width:0; min-height:calc(var(--conversation-target) + var(--space-2) + var(--space-3));
    border:0; padding:var(--space-2) 0 var(--space-3); background:transparent; color:var(--text-muted);
    text-align:left; cursor:pointer; font:var(--type-sm)/1.5 var(--font-ui); }
  .c2-rest-status { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3);
    min-width:0; min-height:var(--conversation-target); }
  .c2-rest-activity { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
  [data-conversation-rest-line] { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
  .c2-rest-control { display:grid; place-items:center; width:var(--conversation-target); height:var(--conversation-target);
    flex:none; margin-right:calc(var(--space-2) * -1); }
  .c2-rest-control svg { width:calc(var(--conversation-glyph) - var(--space-2)); height:calc(var(--conversation-glyph) - var(--space-2));
    fill:none; stroke:currentColor; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
  .c2-rest:focus-visible { outline:var(--border-strong) solid var(--accent-bright); outline-offset:var(--space-1); border-radius:var(--radius-sm); }
  .c2-rest-preview { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; line-clamp:3; overflow:hidden;
    white-space:normal; color:var(--text-default); font:var(--type-md)/1.6 var(--font-ui); }
  .c2-rest-feedback { flex:none; padding-bottom:var(--space-2); color:var(--text-faint); font:var(--type-sm)/1.5 var(--font-ui); }
  .c2-mark--running { flex:none; width:calc(var(--space-2) + var(--border-strong)); height:calc(var(--space-2) + var(--border-strong));
    border-radius:var(--radius-pill); border:1.5px solid var(--text-faintest); border-top-color:var(--text-muted);
    animation:spin var(--motion-loop-spin) linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @media (prefers-reduced-motion:reduce) { .c2-mark--running { animation:none; } }
</style>
