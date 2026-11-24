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
   * turns for as long as the line shows: the server saying the turn runs is the only word it follows. It is drawn as the
   * conversation's one system line (SystemLine).
   */
  import {
    earlierMessagesSentence,
    formatDuration,
    workedSentence
  } from "../../lib/conversation/transcript";
  import { elapsedTurnTime } from "../../lib/conversation/elapsedTime.svelte";
  import SystemLine from "./SystemLine.svelte";

  let {
    activityUncertain = false,
    settled = false,
    stopped = false,
    startedAtUnixMilliseconds = null,
    durationSeconds = null,
    toolCallCount = 0,
    foldedMessageCount = 0,
    expanded = false,
    waiting = null,
    onToggle
  }: {
    activityUncertain?: boolean;
    settled?: boolean;
    stopped?: boolean;
    /** When the turn began, in unix milliseconds, so a reload counts from the truth. */
    startedAtUnixMilliseconds?: number | null;
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
    /** Opens and closes the turn's earlier messages. Absent where the reading shows them. */
    onToggle?: () => void;
  } = $props();

  const elapsed = elapsedTurnTime(() => settled ? null : startedAtUnixMilliseconds);

  let foldLabel = $derived(workedSentence(durationSeconds));
  let foldCount = $derived(onToggle === undefined ? "" : earlierMessagesSentence(foldedMessageCount));
  // A turn that stopped without an ending, with nothing it said or did, has nothing it can
  // honestly say: its own status line already says what happened.
  // A turn held by the person draws no head: the ask docked over the composer says it waits,
  // in its own words, and a clock here would claim progress that is not being made.
  let held = $derived(!settled && waiting !== null && !activityUncertain);
  let visible = $derived(!held && (!settled || !stopped || toolCallCount > 0 || foldedMessageCount > 0));
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
        <SystemLine>
          <button
            type="button"
            class="c2-turn-fold"
            data-conversation-turn-fold
            aria-expanded={expanded}
            onclick={() => onToggle?.()}
          >
            <span data-conversation-turn-label>{foldLabel}</span>
            <span aria-hidden="true">·</span>
            <span>{foldCount}</span>
            <svg class="c2-turn-chevron" class:is-open={expanded} viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
          </button>
        </SystemLine>
      {:else}
        <SystemLine data-conversation-turn-settled-head="">
          <span data-conversation-turn-label>{foldLabel}</span>
        </SystemLine>
      {/if}
    {:else}
      <!-- It turns while the line shows, quiet or not. Unknown
           activity has no mark, because nothing is known to be running. -->
      <SystemLine
        mark={activityUncertain ? null : "spinner"}
        role="status"
        data-conversation-alive=""
      >
        {#if activityUncertain}
          <span data-conversation-alive-word>Current activity on this machine is unknown.</span>
        {:else}
          <span data-conversation-alive-word>Working</span>{#if elapsed.seconds !== null}<span aria-hidden="true">{" · "}</span><span data-conversation-alive-clock>{formatDuration(elapsed.seconds)}</span>{/if}
        {/if}
      </SystemLine>
    {/if}
  </div>
{/if}

<style>
  .c2-turn { display: flex; flex-direction: column; align-items: flex-start; min-width: 0; max-width: 100%; }
  /* The fold is the line itself: no fill, its words brighten when pointed at. */
  .c2-turn-fold {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 0;
    border: 0;
    background: none;
    color: inherit;
    font: inherit;
    cursor: pointer;
    transition: color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-turn-fold:hover { color: var(--cv-system-action-ink); }
  .c2-turn-fold:focus-visible { outline: var(--cv-focus-width) solid var(--cv-accent); outline-offset: 2px; border-radius: var(--cv-r-sm); }
  .c2-turn-chevron {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out);
  }
  .c2-turn-chevron.is-open { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) {
    .c2-turn-fold, .c2-turn-chevron { transition: none; }
  }
</style>
