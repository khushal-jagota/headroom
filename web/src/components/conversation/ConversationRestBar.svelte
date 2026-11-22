<script lang="ts">
  import type { DockStatus } from "../../lib/conversation/dockPresentation";
  import SystemLine from "./SystemLine.svelte";
  /** The dock's status line, at rest on a work page: what the conversation is doing, the way
   * to open it, and the latest unread message under them. It always says one of four states
   * (dockStatusFrom), also while an ask or a proposal waits. */
  let { preview = null, status, askBelow = false, onOpen }: {
    preview?: string | null;
    status: DockStatus;
    /** An ask (permission, question, key) is drawn as the composer under this line. The
     *  unread preview then gives way to it; the status stays. */
    askBelow?: boolean;
    onOpen: () => void;
  } = $props();

  const LABELS = { "setting-up": "Setting up", working: "Working", idle: "Idle", offline: "Machine offline" } as const;
  let live = $derived(status.kind === "setting-up" || status.kind === "working");
</script>

<!-- One row: the state on the left and the way to open the conversation on the right, both
     one button, so the whole line opens it. The unread message sits under it. -->
<button type="button" class="c2-rest" data-conversation-rest-bar data-conversation-expand data-conversation-state-control
  data-conversation-rest-state={status.kind} data-conversation-rest-ask-below={askBelow || undefined}
  aria-label="Open the conversation full height" title="Open the conversation full height" onclick={onOpen}>
  <span class="c2-rest-status">
    <!-- The shared system line, with its mark inline: the dock has no gutter on its left. -->
    <SystemLine mark={live ? "spinner" : "dot"} hangMark={false} data-conversation-rest-line="">{LABELS[status.kind]}{#if status.clock !== null}{" · "}<span data-conversation-rest-clock>{status.clock}</span>{/if}</SystemLine>
    <span class="c2-rest-control" aria-hidden="true">
      <span class="c2-rest-control-label">Open</span>
      <!-- The one exception to the no-chevron rule: a 12px up arrow after the label says the
           conversation opens upward. -->
      <svg class="c2-rest-control-arrow" viewBox="0 0 16 16" data-conversation-open-arrow><path d="m4 10 4-4 4 4" /></svg>
    </span>
  </span>
  {#if !askBelow && preview}<span class="c2-rest-preview" data-conversation-unread-preview>{preview}</span>{/if}
</button>

<style>
  /* The status line: a 40px row over the composer, the state on the left and "Open" on the
     right edge. Space, not a rule, separates it from the page. */
  .c2-rest { position:relative; display:flex; flex:none; flex-direction:column; align-items:stretch; gap:var(--cv-s1);
    width:100%; min-width:0; border:0; padding:0 0 var(--cv-s2); background:transparent; color:#8f8f8f;
    text-align:left; cursor:pointer; font:var(--cv-weight-body) 13px/20px var(--cv-font);
    font-variant-numeric:tabular-nums; }
  /* A short canvas gradient carries the page into the dock, so a line scrolling under it is
     covered within a few pixels instead of cut (the dock has no surface). */
  .c2-rest::before { content:""; position:absolute; left:0; right:0; bottom:100%; height:var(--cv-s5);
    background:linear-gradient(to bottom, transparent, var(--cv-canvas)); pointer-events:none; }
  .c2-rest-status { display:flex; align-items:center; justify-content:space-between; gap:var(--cv-s3);
    min-width:0; min-height:40px; }
  /* "Open" is a quiet text control: no fill and no edge at rest, a fill on hover. */
  .c2-rest-control { flex:none; display:inline-flex; align-items:center; gap:6px; height:32px; padding:0 12px 0 14px;
    border:0; border-radius:999px; background:transparent; color:#d0d0d0; font-weight:var(--cv-weight-label);
    transition:color var(--cv-dur-fast) var(--cv-ease-out), background-color var(--cv-dur-fast) var(--cv-ease-out); }
  .c2-rest-control-arrow { flex:none; width:12px; height:12px; fill:none; stroke:currentColor; stroke-width:1.5; stroke-linecap:round; stroke-linejoin:round; }
  .c2-rest:hover .c2-rest-control, :global([data-conversation-dock-hover]) .c2-rest-control, .c2-rest:focus-visible .c2-rest-control {
    color:#ececec; background-color:#2a2a2a; }
  .c2-rest:focus-visible { outline:var(--cv-focus-width) solid var(--cv-accent); outline-offset:var(--cv-focus-offset); border-radius:var(--cv-r-md); }
  /* The unread message keeps the prose measure, never the pane's width. */
  .c2-rest-preview { max-width:var(--page-prose-measure); display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; line-clamp:3; overflow:hidden;
    white-space:normal; color:var(--cv-t2); font:var(--cv-weight-body) var(--cv-type-ui)/var(--cv-leading-ui) var(--cv-font); }
</style>
