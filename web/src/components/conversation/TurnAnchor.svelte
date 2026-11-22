<script lang="ts">
  /** A turn's head: the one line in the thread that does not move.
   *
   * It appears the moment the turn starts, before there is anything to put under it, and
   * it is still there when the turn is over. It binds to what follows it — the turn's
   * first words sit 8 px under it — so it reads as that turn's line, never as a stray
   * caption between two turns.
   *
   * It speaks about the turn, not about its tools. Tool calls are Full's to show, under
   * their own summaries, so the head never counts them and never opens onto them. In Focus
   * a settled turn that said more than its answer folds those earlier messages, and the
   * head is the one place to open them.
   *
   * While the turn runs it counts, honestly, from the moment the prompt landed. The mark
   * turns while live frames arrive and holds still when they stop; the label shimmers only
   * while it is alive. Waiting on the person is the one state it says in the accent.
   */
  import {
    earlierMessagesSentence,
    formatDuration,
    turnFoldLabel
  } from "../../lib/conversation/transcript";
  import { elapsedTurnTime } from "../../lib/conversation/elapsedTime.svelte";
  import type { ConversationTurnEnding } from "../../lib/conversation/wire";

  /** How long a sign of life is still recent. Long enough to ride out the gaps between
   *  frames, short enough that a stalled backend stops claiming to be alive. */
  const LIVELINESS_DECAY_MS = 2_000;

  let {
    activityUncertain = false,
    settled = false,
    stopped = false,
    startedAtUnixMilliseconds = null,
    ending = null,
    isLatest = false,
    durationSeconds = null,
    toolCallCount = 0,
    foldedMessageCount = 0,
    expanded = false,
    waiting = null,
    livenessPulse = 0,
    onToggle
  }: {
    activityUncertain?: boolean;
    settled?: boolean;
    stopped?: boolean;
    /** When the turn began, in unix milliseconds, so a reload counts from the truth. */
    startedAtUnixMilliseconds?: number | null;
    ending?: ConversationTurnEnding | null;
    isLatest?: boolean;
    durationSeconds?: number | null;
    /** Whether the turn did any work. Never said: it only keeps the head of a turn that
     *  stopped without an ending, because its work is still there to be read in Full. */
    toolCallCount?: number;
    /** How much of what the turn said is behind this fold, once the turn has settled. */
    foldedMessageCount?: number;
    /** Whether this turn's earlier messages are showing. */
    expanded?: boolean;
    /** What the running turn is waiting on the person for, if anything. A turn held by a
     *  question or an approval is not working, and its head must not say it is. */
    waiting?: "permission" | "question" | null;
    /** Moves whenever a live frame arrives. Only its movement is read. */
    livenessPulse?: number;
    /** Opens and closes the turn's earlier messages. Absent where the reading shows them. */
    onToggle?: () => void;
  } = $props();

  let fresh = $state(true);
  const elapsed = elapsedTurnTime(() => settled ? null : startedAtUnixMilliseconds);

  let foldLabel = $derived(turnFoldLabel({ durationSeconds, ending, isLatest }));
  let foldCount = $derived(onToggle === undefined ? "" : earlierMessagesSentence(foldedMessageCount));
  // A turn that stopped without an ending, with nothing it said or did, has nothing it can
  // honestly say: its own status line already says what happened.
  // A turn held by the person draws no head: the ask docked over the composer says it waits,
  // in its own words, and a clock here would claim progress that is not being made.
  let held = $derived(!settled && waiting !== null && !activityUncertain);
  let visible = $derived(!held && (!settled || !stopped || toolCallCount > 0 || foldedMessageCount > 0));
  let alive = $derived(fresh && !activityUncertain);

  $effect(() => {
    livenessPulse;
    fresh = true;
    const decay = setTimeout(() => (fresh = false), LIVELINESS_DECAY_MS);
    return () => clearTimeout(decay);
  });
</script>

{#if visible}
  <div
    class="c2-turn"
    data-conversation-turn
    data-conversation-turn-settled={settled ? "true" : "false"}
    data-conversation-turn-stopped={stopped ? "true" : undefined}
    data-conversation-turn-expanded={expanded ? "true" : "false"}
  >
    {#if settled}
      {#if foldCount !== ""}
        <!-- The one place to open what this turn said before its answer. -->
        <button
          type="button"
          class="c2-meta c2-meta--fold"
          data-conversation-turn-fold
          aria-expanded={expanded}
          onclick={() => onToggle?.()}
        >
          <span data-conversation-turn-label>{foldLabel}</span>
          <span class="c2-meta-sep" aria-hidden="true">·</span>
          <span class="c2-meta-detail">{foldCount}</span>
          <svg class="c2-meta-chevron" class:is-open={expanded} viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
        </button>
      {:else}
        <div class="c2-meta" data-conversation-turn-settled-head>
          <span data-conversation-turn-label>{foldLabel}</span>
        </div>
      {/if}
    {:else}
      <div
        class="c2-meta c2-meta--live"
        data-conversation-alive
        data-conversation-alive-fresh={alive ? "true" : "false"}
        role="status"
      >
        <!-- It turns only while frames arrive; when they stop it holds still. Unknown
             activity has no mark, because nothing is known to be running. -->
        {#if !activityUncertain}<span class="c2-meta-slot" aria-hidden="true"><span class="c2-meta-spin" class:is-turning={fresh} data-conversation-live-mark></span></span>{/if}
        {#if activityUncertain}
          <span data-conversation-alive-word>Current activity on this machine is unknown.</span>
        {:else}
          <span class="c2-meta-working" class:is-alive={alive} data-conversation-alive-word>Working</span>
          {#if elapsed.seconds !== null}
            <span class="c2-meta-sep" aria-hidden="true">·</span>
            <span class="c2-meta-clock" data-conversation-alive-clock>{formatDuration(elapsed.seconds)}</span>
          {/if}
        {/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .c2-turn { display: flex; flex-direction: column; align-items: flex-start; min-width: 0; max-width: 100%; }
  /* One layout for every state: [mark] label · detail [chevron], 13/20 in the meta tone. */
  /* v7 status line: 13/20, a 16px icon slot, the label 500 #ececec, " · ", the detail 400
     #b0b0b0. "Worked for 2m 20s" is detail-only: no icon, #b0b0b0. */
  .c2-meta {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    max-width: 100%;
    min-height: 20px;
    color: var(--cv-detail-ink, #b0b0b0);
    font-family: var(--cv-font);
    font-size: 13px;
    line-height: 20px;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }
  .c2-meta > span { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .c2-meta-sep { margin-inline: -2px; color: var(--cv-detail-ink, #b0b0b0); }
  .c2-meta-slot { flex: none; display: inline-grid; place-items: center; width: 16px; height: 16px; }
  /* The fold is a quiet control: the line itself, with a hover fill that reaches past
     its text so the target is as large as the line, not as small as its words. */
  .c2-meta--fold {
    margin-inline: -8px;
    padding: 0 8px;
    min-height: 24px;
    border: 0;
    border-radius: 999px;
    background: transparent;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out), color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-meta--fold:hover { background: #2a2a2a; color: var(--cv-ink, #ececec); }
  .c2-meta--fold:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 0; color: var(--cv-t2); }
  .c2-meta-chevron {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--cv-quiet-ink, #bdbdbd);
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out), stroke var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-meta--fold:hover .c2-meta-chevron, .c2-meta--fold:focus-visible .c2-meta-chevron { stroke: var(--cv-ink, #ececec); }
  .c2-meta-chevron.is-open { transform: rotate(90deg); }
  /* Working has no colour: it is said by motion and by a brighter tone. */
  /* v5.1: a 12px spinner and "Working · 2m 14s" at the top of the live turn, where "Worked
     for 7m" stands on a finished one. */
  .c2-meta-spin {
    flex: none;
    box-sizing: border-box;
    width: 12px;
    height: 12px;
    border-radius: var(--cv-r-pill);
    /* v6: one spinner in the conversation: the arc #d9d9d9 on a #4a4a4a track. */
    border: 2px solid #4a4a4a;
    border-top-color: #d9d9d9;
    transform: rotate(35deg);
  }
  .c2-meta-spin.is-turning { animation: c2-meta-spin var(--cv-spin) linear infinite; }
  @keyframes c2-meta-spin { to { transform: rotate(395deg); } }
  /* v5.2: "Working" 13 medium #d9d9d9, its duration #b0b0b0. "Worked for" 13 regular #b0b0b0. */
  .c2-meta-working { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }
  .c2-meta-working.is-alive {
    background: linear-gradient(90deg, #ececec 0%, #ececec 35%, #ffffff 50%, #ececec 65%, #ececec 100%);
    background-size: 200% 100%;
    -webkit-background-clip: text;
            background-clip: text;
    color: transparent;
    animation: c2-meta-shimmer var(--cv-shimmer) linear infinite;
  }
  @keyframes c2-meta-shimmer { from { background-position: 100% 0; } to { background-position: -100% 0; } }
  .c2-meta-clock { color: var(--cv-detail-ink, #b0b0b0); }
  @media (prefers-reduced-motion: reduce) {
    .c2-meta--fold, .c2-meta-chevron { transition: none; }
    .c2-meta-spin.is-turning { animation: none; }
    .c2-meta-working.is-alive { animation: none; background: none; color: #ececec; }
  }
</style>
