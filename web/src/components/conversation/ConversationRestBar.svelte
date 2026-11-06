<script lang="ts">
  /** Activity and the unread preview belong to the conversation surface.
   * Delivery and request feedback remains independent of the control that opens it. */
  let { preview = null, roleLabel = "Worker", activityKnown = true, connectionTrouble = false,
    running = false, backgroundWork = null, feedback = null, onOpen }: { preview?: string | null;
    roleLabel?: "Worker" | "Manager" | "Chief" | "Goal"; activityKnown?: boolean; connectionTrouble?: boolean;
    running?: boolean; backgroundWork?: string | null; feedback?: string | null; onOpen: () => void } = $props();
  let activity = $derived(running ? "Working" : backgroundWork ?? (activityKnown ? `${roleLabel} idle` : null));
</script>

<!-- The composer's own header line: what the agent is doing, then the latest message on the
     same line, then the way to open the conversation. One line on every page. -->
<button type="button" class="c2-rest" data-conversation-rest-bar data-conversation-expand data-conversation-state-control
  aria-label="Open the conversation full height" title="Open the conversation full height" onclick={onOpen}>
  <span class="c2-rest-status">
    <span class="c2-rest-activity">
      <!-- An unread message from the agent: the accent dot leads its status, where the
           message itself is previewed, never floating beside a page title. -->
      {#if preview}<span class="c2-rest-unread" role="img" aria-label="Unread message" data-conversation-unread-dot></span>{/if}
      {#if running || backgroundWork}<span class="c2-mark--running" aria-label="working"></span>{/if}
      {#if connectionTrouble}<span class="chat-conn-dot" role="img" aria-label="Connection trouble"></span>{/if}
      {#if activity}<span data-conversation-rest-line>{activity}</span>{/if}
    </span>
    {#if preview}<span class="c2-rest-preview" data-conversation-unread-preview>{preview}</span>{/if}
    <!-- The way to open the conversation: a ghost chevron right after the status words. The
         whole line is the control, so this is its mark, sharing the line's tooltip. -->
    <span class="c2-rest-control" aria-hidden="true">
      <svg viewBox="0 0 16 16"><path d="M4 10l4-4 4 4" /></svg>
    </span>
  </span>
</button>
{#if feedback}<div class="c2-rest-feedback" role="status" data-conversation-dock-feedback>{feedback}</div>{/if}

<style>
  /* The status line is the composer's own small print: Small, tertiary, one line. Where it is
     the floating composer's footer it starts on the composer's left edge (app.css), so it reads
     as part of the composer zone and never as the last line of the page. */
  .c2-rest { display:flex; flex:none; flex-direction:column; align-items:stretch;
    width:100%; min-width:0;
    border:0; border-radius:var(--radius-md); padding:0 var(--composer-inset) var(--composer-status-gap); background:transparent; color:var(--tone-tertiary);
    text-align:left; cursor:pointer; font-family:var(--font-ui); font-size:var(--type-small); line-height:18px; font-weight:var(--weight-small); font-variant-numeric:tabular-nums; }
  .c2-rest-status { display:flex; align-items:center; gap:var(--space-2); min-width:0; min-height:24px; }
  .c2-rest-activity { flex:none; display:flex; align-items:center; gap:var(--space-2); min-width:0; }
  [data-conversation-rest-line] { white-space:nowrap; }
  /* The latest message follows the status on the same line, in the same tone, and ends in an
     ellipsis where the line does. */
  .c2-rest-preview { flex:0 1 auto; min-width:0; max-width:var(--composer-excerpt); overflow:hidden; white-space:nowrap; text-overflow:ellipsis; color:var(--tone-tertiary); }
  .c2-rest-unread { flex:none; width:6px; height:6px; border-radius:var(--radius-pill); background:var(--accent-bright); }
  .c2-rest-preview::before { content:"·"; margin-right:var(--space-2); color:var(--tone-tertiary); }
  /* A 24px ghost icon button right after the words: no fill at rest, one step on hover. */
  .c2-rest-control { display:grid; place-items:center; width:24px; height:24px; margin-left:calc(var(--space-1) - var(--space-2));
    flex:none; border:0; border-radius:var(--radius-md); color:var(--tone-secondary); background-color:transparent;
    transition:color var(--motion-base) var(--motion-ease), background-color var(--motion-base) var(--motion-ease); }
  .c2-rest:hover .c2-rest-control, :global([data-conversation-dock-hover]) .c2-rest-control, .c2-rest:focus-visible .c2-rest-control {
    color:var(--tone-strong); background-color:var(--wash-hover); }
  .c2-rest-control svg { width:14px; height:14px;
    fill:none; stroke:currentColor; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
  .c2-rest:focus-visible { outline:var(--focus-width) solid var(--focus-ring); outline-offset:2px; }
  .c2-rest-feedback { flex:none; padding:0 var(--composer-inset) var(--composer-status-gap); color:var(--tone-tertiary); font:var(--weight-small) var(--type-small)/18px var(--font-ui); }
  .c2-mark--running { flex:none; width:calc(var(--space-2) + var(--border-strong)); height:calc(var(--space-2) + var(--border-strong));
    border-radius:var(--radius-pill); border:1.5px solid var(--tone-tertiary); border-top-color:var(--tone-secondary);
    animation:spin var(--motion-loop-spin) linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  @media (max-height:520px) { .c2-rest-preview { display:none; } .c2-rest { padding-bottom:var(--space-1); } }
  @media (prefers-reduced-motion:reduce) { .c2-mark--running { animation:none; } .c2-rest-control { transition:none; } }
</style>
