<script lang="ts">
  /** Activity and the unread preview belong to the conversation surface.
   * Delivery and request feedback remains independent of the control that opens it. */
  let { preview = null, roleLabel = "Worker", activityKnown = true, connectionTrouble = false,
    running = false, backgroundWork = null, feedback = null, onOpen }: { preview?: string | null;
    roleLabel?: "Worker" | "Manager" | "Chief" | "Goal"; activityKnown?: boolean; connectionTrouble?: boolean;
    running?: boolean; backgroundWork?: string | null; feedback?: string | null; onOpen: () => void } = $props();
  let activity = $derived(running ? "Working" : backgroundWork ?? (activityKnown ? `${roleLabel} idle` : null));
</script>

<!-- The status strip is the top of the composer: a quieter tab the field overlaps, so the
     line, the latest message and the field are one object at the foot of the page. -->
<div class="c2-strip" data-conversation-strip>
<button type="button" class="c2-rest" data-conversation-rest-bar data-conversation-expand data-conversation-state-control
  aria-label="Open the conversation full height" title="Open the conversation full height" onclick={onOpen}>
  <span class="c2-rest-status">
    <span class="c2-rest-activity">
      {#if running || backgroundWork}<span class="c2-mark--running" aria-label="working"></span>{/if}
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
</div>

<style>
  /* The strip sits a step inside the field's edges and runs under its top, the way a tab
     sits behind a card: one plane quieter than the field, on the page's own plane. */
  .c2-strip { position:relative; z-index:0; flex:none; margin:0 var(--space-3) calc(-1 * var(--space-3));
    padding-bottom:var(--space-3); background:var(--surface-2); border:var(--border-hairline) solid var(--border-color);
    border-bottom:0; border-radius:var(--radius-lg) var(--radius-lg) 0 0; }
  .c2-strip + :global(*) { position:relative; z-index:1; }
  .c2-rest { display:flex; flex:none; flex-direction:column; align-items:stretch; gap:2px;
    width:100%; min-width:0;
    border:0; border-radius:var(--radius-lg) var(--radius-lg) 0 0; padding:6px var(--space-2) var(--space-2) var(--space-4); background:transparent; color:var(--text-faint);
    text-align:left; cursor:pointer; font:var(--type-sm)/1.5 var(--font-ui); font-weight:500; }
  .c2-rest-status { display:flex; align-items:center; justify-content:space-between; gap:var(--space-3);
    min-width:0; min-height:28px; }
  .c2-rest-activity { display:flex; align-items:center; gap:var(--space-2); min-width:0; }
  [data-conversation-rest-line] { overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
  /* The arrow alone answers dock hover and rest-bar focus. */
  .c2-rest-control { display:grid; place-items:center; width:28px; height:28px;
    flex:none; border:0; border-radius:var(--radius-md); color:var(--text-faint); background-color:transparent;
    transition:color var(--motion-base) var(--motion-ease), background-color var(--motion-base) var(--motion-ease); }
  :global([data-conversation-dock-hover]) .c2-rest-control, .c2-rest:focus-visible .c2-rest-control {
    color:var(--text-strong); background-color:var(--wash-selected); }
  .c2-rest-control svg { width:calc(var(--conversation-glyph) - var(--space-2)); height:calc(var(--conversation-glyph) - var(--space-2));
    fill:none; stroke:currentColor; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
  .c2-rest:focus-visible { outline:var(--border-strong) solid var(--focus-ring); outline-offset:-2px; }
  /* The latest message is the strip's body: one size under the field, a muted tone. */
  .c2-rest-preview { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:2; line-clamp:2; overflow:hidden;
    white-space:normal; color:var(--text-muted); font:var(--type-sm)/20px var(--font-ui); font-weight:500; padding:0 var(--space-3) var(--space-1) 0; }
  .c2-rest-feedback { flex:none; padding:0 var(--space-4) var(--space-2); color:var(--text-faint); font:var(--type-sm)/1.5 var(--font-ui); }
  .c2-mark--running { flex:none; width:calc(var(--space-2) + var(--border-strong)); height:calc(var(--space-2) + var(--border-strong));
    border-radius:var(--radius-pill); border:1.5px solid var(--text-faintest); border-top-color:var(--text-muted);
    animation:spin var(--motion-loop-spin) linear infinite; }
  @keyframes spin { to { transform:rotate(360deg); } }
  /* A short window (a zoomed browser, a small laptop) keeps the dock a small share of the
     height: one preview line, then none, so the document always keeps the larger part. */
  @media (max-height:720px) { .c2-rest-preview { -webkit-line-clamp:1; line-clamp:1; } }
  @media (max-height:520px) { .c2-rest-preview { display:none; } .c2-rest { padding-block:2px var(--space-1); } }
  @media (prefers-reduced-motion:reduce) { .c2-mark--running { animation:none; } .c2-rest-control { transition:none; } }
</style>
