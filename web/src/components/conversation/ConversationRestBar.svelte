<script lang="ts">
  import type { ConversationStateReading } from "../../lib/conversation/dockPresentation";
  /** The dock's status line, at rest on a work page: what the conversation is doing (the same
   * derived state the opened pane's header reads), the way to open it, and the latest unread
   * message under them. Its words never name a tool or a command, in either lens. */
  let { preview = null, roleLabel = "Worker", state, connectionTrouble = false,
    waitingTitle = null, offline = false, feedback = null, pageHoldsTheAsk = false, onOpen }: {
    /** The page above draws the waiting ask itself (a proposal to approve): the dock does not
     *  say it a second time, and keeps only the way into the conversation. */
    pageHoldsTheAsk?: boolean;
    preview?: string | null;
    roleLabel?: "Worker" | "Manager" | "Chief" | "Goal";
    /** The conversation's one state (conversationState in dockPresentation). */
    state: ConversationStateReading;
    connectionTrouble?: boolean;
    /** What a waiting ask is about, said after "Needs your approval". */
    waitingTitle?: string | null;
    /** The machine cannot be read. Setup still says setup, with one caution chip. */
    offline?: boolean;
    /** The newest exceptional thing that happened, in its own words, or the reason the
     *  machine cannot be read. */
    feedback?: string | null;
    onOpen: () => void;
  } = $props();

  const ROLE_NAMES = { Worker: "Worker", Manager: "Manager", Chief: "Chief of Staff", Goal: "Goal" } as const;
  let problem = $derived(state.state === "failed" || state.state === "undelivered");
  /** The words after the label: what the waiting ask is about, or why it failed. */
  let detail = $derived(
    state.state === "waiting" ? waitingTitle
    : problem || state.state === "interrupted" ? (feedback?.replace(/^not delivered · /, "") || null)
    : null
  );
  /** The setup the machine cannot confirm keeps saying setup; one chip says why it is still. */
  let offlineChip = $derived(offline && state.state === "setting-up");
  /** The page above draws the waiting ask (a proposal): v4 has the line say "Waiting for your
   *  approval" with the accent dot, and not repeat the proposal's title. */
  let pageWaits = $derived(pageHoldsTheAsk && state.state === "waiting");
  let saysState = $derived(true);
  let label = $derived(
    pageWaits ? "Waiting for your approval"
    : state.state === "idle" ? `${ROLE_NAMES[roleLabel]} idle`
    : state.state === "unknown" ? (feedback ?? state.text)
    : state.text
  );
  /** A second line only for news the label does not already carry (a message whose delivery
   *  is uncertain, one that was discarded). */
  let aside = $derived(
    feedback !== null && detail === null && !offline && state.state !== "unknown" ? feedback : null
  );
</script>

<!-- One row: the state on the left and the way to open the conversation on the right, both
     one button, so the whole line opens it. The unread message sits under it. -->
<button type="button" class="c2-rest" data-conversation-rest-bar data-conversation-expand data-conversation-state-control
  data-conversation-rest-state={state.state}
  aria-label="Open the conversation full height" title="Open the conversation full height" onclick={onOpen}>
  <span class="c2-rest-status">
    <span class="c2-rest-activity">
      {#if saysState && state.mark === "spinner"}<span class="cv-spinner" role="img" aria-label={state.state === "setting-up" ? (offline ? "setting up, not confirmed" : "setting up") : "working"} data-unconfirmed={offlineChip || undefined}></span>
      {:else if saysState && state.mark === "dot"}<span class="cv-dot" aria-hidden="true"></span>
      {:else if state.mark === "alert"}<svg class="cv-glyph" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.25" /><path d="M8 4.9v3.6" /><path d="M8 11.1v.1" /></svg>
      {:else if state.mark === "stop"}<svg class="cv-glyph" viewBox="0 0 16 16" aria-hidden="true"><rect x="3.5" y="3.5" width="9" height="9" rx="2" /></svg>
      {:else if state.state === "unknown"}<svg class="cv-glyph cv-glyph--caution" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.2 14.3 13H1.7z" /><path d="M8 6.5v3" /><path d="M8 11.4v.1" /></svg>{/if}
      {#if saysState || pageWaits}
      <span class="c2-rest-line" data-conversation-rest-line data-conversation-rest-page-waits={pageWaits || undefined}>
        <span class="c2-rest-label">{label}</span>{#if detail && !pageWaits}<span class="c2-rest-detail">{` — ${detail}`}</span>{/if}
      </span>
      {/if}
      {#if offlineChip}<span class="c2-rest-chip" title={feedback ?? undefined} data-conversation-offline-chip>Machine offline</span>{/if}
      {#if connectionTrouble}<svg class="cv-glyph cv-glyph--danger" viewBox="0 0 16 16" role="img" aria-label="Connection trouble"><path d="M2.5 6.2a8 8 0 0 1 11 0" /><path d="M4.6 8.6a5 5 0 0 1 6.8 0" /><path d="M8 11.6v.1" /><path d="m3 3 10 10" /></svg>{/if}
    </span>
    <span class="c2-rest-control" aria-hidden="true">
      <span class="c2-rest-control-label">Open conversation</span>
      <svg viewBox="0 0 16 16"><path d="m4 10 4-4 4 4" /></svg>
    </span>
  </span>
  {#if preview}<span class="c2-rest-preview" data-conversation-unread-preview>{preview}</span>{/if}
</button>
{#if aside}<div class="c2-rest-feedback" role="status" data-conversation-dock-feedback>{aside}</div>{/if}
{#if offlineChip && feedback}<span class="c2-rest-offline-reason" role="status" data-conversation-dock-feedback>{feedback}</span>{/if}

<style>
  /* The status line: a 40px row of meta type over the composer, the state on the left and
     "Open conversation" on the right edge. Space, not a rule, separates it from the page. */
  .c2-rest { position:relative; display:flex; flex:none; flex-direction:column; align-items:stretch; gap:var(--cv-s1);
    width:100%; min-width:0; border:0; padding:0 0 var(--cv-s2); background:transparent; color:var(--cv-t3);
    text-align:left; cursor:pointer; font:var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font);
    font-variant-numeric:tabular-nums; }
  /* A short canvas gradient carries the page into the dock, so a line scrolling under it is
     covered within a few pixels instead of cut. */
  .c2-rest::before { content:""; position:absolute; left:0; right:0; bottom:100%; height:var(--cv-s5);
    background:linear-gradient(to bottom, transparent, var(--cv-canvas)); pointer-events:none; }
  .c2-rest-status { display:flex; align-items:center; justify-content:space-between; gap:var(--cv-s3);
    min-width:0; min-height:40px; }
  .c2-rest-activity { display:flex; align-items:center; gap:var(--cv-s2); min-width:0; }
  .c2-rest-line { min-width:0; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
  .c2-rest-label { color:var(--cv-t3); }
  .c2-rest-detail { color:var(--cv-t3); }
  /* Tone follows the state, never a line: working is the brighter text and its motion,
     waiting is the accent with its title in t2, a failure is the danger colour. */
  [data-conversation-rest-state="working"] .c2-rest-label { color:var(--cv-t2); }
  [data-conversation-rest-state="waiting"] .c2-rest-label { color:var(--cv-accent); font-weight:var(--cv-weight-label); }
  [data-conversation-rest-state="waiting"] .c2-rest-detail { color:var(--cv-t2); }
  :is([data-conversation-rest-state="failed"], [data-conversation-rest-state="undelivered"]) .c2-rest-label { color:var(--cv-danger); font-weight:var(--cv-weight-label); }
  :is([data-conversation-rest-state="failed"], [data-conversation-rest-state="undelivered"]) .cv-glyph { color:var(--cv-danger); }
  [data-conversation-rest-state="unknown"] .c2-rest-label { color:var(--cv-t2); }
  .c2-rest-chip { flex:none; display:inline-flex; align-items:center; height:24px; padding:0 var(--cv-s2);
    border-radius:var(--cv-r-sm); background:color-mix(in srgb, var(--cv-caution) 12%, transparent); color:var(--cv-caution);
    font:var(--cv-weight-label) var(--cv-type-caption)/var(--cv-leading-caption) var(--cv-font); }
  /* The way to open it: a ghost with its label on a wide screen, the chevron alone on a narrow one. */
  .c2-rest-control { flex:none; display:inline-flex; align-items:center; gap:var(--cv-s1); height:32px; padding:0 var(--cv-s2) 0 var(--cv-s3);
    border-radius:var(--cv-r-md); color:var(--cv-t2); font-weight:var(--cv-weight-label);
    transition:color var(--cv-dur-fast) var(--cv-ease-out), background-color var(--cv-dur-fast) var(--cv-ease-out); }
  .c2-rest:hover .c2-rest-control, :global([data-conversation-dock-hover]) .c2-rest-control, .c2-rest:focus-visible .c2-rest-control {
    color:var(--cv-t1); background-color:var(--cv-control); }
  .c2-rest-control svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.6; stroke-linecap:round; stroke-linejoin:round; }
  @media (max-width:1023px) {
    .c2-rest-control { width:32px; padding:0; justify-content:center; }
    .c2-rest-control-label { display:none; }
  }
  .c2-rest:focus-visible { outline:var(--cv-focus-width) solid var(--cv-accent); outline-offset:var(--cv-focus-offset); border-radius:var(--cv-r-md); }
  /* The unread message keeps the prose measure, never the pane's width. */
  .c2-rest-preview { max-width:var(--page-prose-measure); display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; line-clamp:3; overflow:hidden;
    white-space:normal; color:var(--cv-t2); font:var(--cv-weight-body) var(--cv-type-ui)/var(--cv-leading-ui) var(--cv-font); }
  .c2-rest-feedback { flex:none; padding-bottom:var(--cv-s2); color:var(--cv-t3); font:var(--cv-weight-body) var(--cv-type-meta)/var(--cv-leading-meta) var(--cv-font); }
  /* The reason behind the chip, for a screen reader: said once, not drawn twice. */
  .c2-rest-offline-reason { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); white-space:nowrap; }
</style>
