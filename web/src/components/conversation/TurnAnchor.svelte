<script lang="ts">
  /** A turn's head: the one place in the thread that does not move.
   *
   * It appears the moment the turn starts, before there is anything to put under it —
   * the wait before the first tool call is exactly the silence it exists to fill — and it
   * is still there when the turn is over, as the fold across everything the turn did: its
   * tool calls, and everything it said on the way to the answer it left standing.
   *
   * While the turn runs it counts, honestly, from the moment the prompt landed: a reload
   * mid-turn shows the real elapsed time rather than starting again from zero. The dots
   * beside it say the model was alive recently; they never claim to track progress nobody
   * can see.
   *
   * It never removes itself. A turn that folded nothing away has nothing to open, but it
   * still says how long it took, in the place it has occupied since the turn began — the
   * head is the same line all the way through, and a line that disappears the instant a
   * turn ends moves everything under it for no reason anybody reading could name.
   */
  import PlanStrip from "./PlanStrip.svelte";
  import {
    elapsedSecondsSince,
    foldedWorkSentence,
    millisecondsUntilNextSecond,
    turnFoldLabel,
    workingSentence
  } from "../../lib/conversation/transcript";
  import type { ConversationTurnEnding, PlanEntry } from "../../lib/conversation/wire";

  /** How long a sign of life is still recent. Long enough to ride out the gaps between
   *  frames, short enough that a stalled backend stops claiming to be alive. */
  const LIVELINESS_DECAY_MS = 2_000;

  let {
    settled = false,
    stopped = false,
    plan = null,
    startedAtUnixMilliseconds = null,
    ending = null,
    isLatest = false,
    durationSeconds = null,
    toolCallCount = 0,
    foldedMessageCount = 0,
    expanded = false,
    livenessPulse = 0,
    onToggle
  }: {
    settled?: boolean;
    stopped?: boolean;
    /** The plan as it stands, when this head is the one holding the newest. */
    plan?: readonly PlanEntry[] | null;
    /** When the turn began, in unix milliseconds, so a reload counts from the truth and
     *  the count turns over on that instant's own seconds. */
    startedAtUnixMilliseconds?: number | null;
    ending?: ConversationTurnEnding | null;
    isLatest?: boolean;
    durationSeconds?: number | null;
    toolCallCount?: number;
    /** How much of what the turn said is behind this fold. Everything the turn said but
     *  the last of it, once the turn has settled. */
    foldedMessageCount?: number;
    /** Whether this turn's work is showing. The turn owns it, not the individual runs. */
    expanded?: boolean;
    /** Moves whenever a live frame arrives. Only its movement is read. */
    livenessPulse?: number;
    onToggle?: () => void;
  } = $props();

  let fresh = $state(false);
  // The one thing that changes every second. The transcript is not rebuilt on a tick and
  // no row is touched: only this number moves.
  let elapsedSeconds = $state<number | null>(null);

  let hasPlan = $derived(plan !== null && plan.length > 0);
  let foldLabel = $derived(turnFoldLabel({ durationSeconds, ending, isLatest }));
  let countLabel = $derived(foldedWorkSentence(toolCallCount, foldedMessageCount));
  // A turn that only talked folds too: five paragraphs of commentary is exactly as long
  // to scroll past as five tool calls.
  let somethingBehindTheFold = $derived(toolCallCount > 0 || foldedMessageCount > 0);
  let foldVisible = $derived(settled && somethingBehindTheFold);
  // The head is there for every turn but one: a turn that stopped without an ending, with
  // nothing behind it and no plan, has nothing it can honestly say. It still keeps its
  // fold when it did work, because that work is behind the fold and this is the only way
  // to reach it.
  let visible = $derived(!settled || !stopped || foldVisible || hasPlan);

  $effect(() => {
    livenessPulse;
    fresh = true;
    const decay = setTimeout(() => (fresh = false), LIVELINESS_DECAY_MS);
    return () => clearTimeout(decay);
  });

  $effect(() => {
    if (settled || startedAtUnixMilliseconds === null) {
      elapsedSeconds = null;
      return;
    }
    const begun = startedAtUnixMilliseconds;
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

{#if visible}
  <div
    class="c2-turn"
    data-conversation-turn
    data-conversation-turn-settled={settled ? "true" : "false"}
    data-conversation-turn-stopped={stopped ? "true" : undefined}
    data-conversation-turn-expanded={expanded ? "true" : "false"}
  >
    {#if foldVisible}
      <button
        type="button"
        class="c2-turn-fold"
        data-conversation-turn-fold
        aria-expanded={expanded}
        onclick={() => onToggle?.()}
      >
        <span aria-hidden="true" class="c2-turn-chevron" class:is-open={expanded}>›</span>
        <span data-conversation-turn-label>{foldLabel}</span>
        {#if expanded}
          <span class="c2-turn-count" data-conversation-turn-count>{countLabel}</span>
        {/if}
      </button>
    {:else if settled && !stopped}
      <!-- Nothing was folded away, so there is nothing to open — but the head stays where
           it has been since the turn began. It is the same line that was counting a moment
           ago, and a line that removes itself the instant a turn ends moves everything
           under it for no reason. The chevron keeps its width so the label sits where the
           openable ones do.
           A turn that stopped without an ending is the one exception: it has no length
           anybody can claim, its own row already says what happened, and "Worked" over a
           dead process would be the head telling a story nobody can stand behind. -->
      <div class="c2-turn-fold c2-turn-settled" data-conversation-turn-settled-head>
        <span aria-hidden="true" class="c2-turn-chevron is-absent">›</span>
        <span data-conversation-turn-label>{foldLabel}</span>
      </div>
    {:else if !settled}
      <div
        class="c2-alive"
        class:is-fresh={fresh}
        data-conversation-alive
        data-conversation-alive-fresh={fresh ? "true" : "false"}
        role="status"
      >
        <span class="c2-alive-dots" aria-hidden="true">
          <span></span><span></span><span></span>
        </span>
        <span class="c2-alive-word" data-conversation-alive-word>
          {workingSentence(elapsedSeconds)}
        </span>
      </div>
    {/if}

    {#if plan}
      <PlanStrip entries={plan} />
    {/if}
  </div>
{/if}

<style>
  .c2-turn { display: grid; min-width: 0; max-width: 100%; gap: var(--space-1); }
  .c2-alive {
    justify-self: start;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    padding: var(--space-1) var(--space-2);
  }
  .c2-alive-word { font-variant-numeric: tabular-nums; }
  .c2-alive-dots { display: inline-flex; align-items: center; gap: var(--space-1); flex: none; }
  .c2-alive-dots span {
    width: var(--space-1);
    height: var(--space-1);
    border-radius: var(--radius-pill);
    background: var(--text-faintest);
    animation: c2-alive-pulse var(--motion-loop-bounce) var(--motion-ease) infinite;
  }
  .c2-alive-dots span:nth-child(2) { animation-delay: calc(var(--motion-loop-bounce) / 6); }
  .c2-alive-dots span:nth-child(3) { animation-delay: calc(var(--motion-loop-bounce) / 3); }
  /* Recently alive: the same dots, brighter. Nothing bounces. */
  .c2-alive.is-fresh { color: var(--text-muted); }
  .c2-alive.is-fresh .c2-alive-dots span { background: var(--accent-bright); }
  @keyframes c2-alive-pulse {
    0%, 100% { opacity: 0.25; }
    50% { opacity: 1; }
  }
  .c2-turn-fold {
    justify-self: start;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    background: transparent;
    border: 0;
    border-radius: var(--radius-sm);
    color: var(--text-faintest);
    cursor: pointer;
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    padding: var(--space-1) var(--space-2);
    font-variant-numeric: tabular-nums;
  }
  .c2-turn-fold:hover { color: var(--text-muted); background: var(--surface-overlay); }
  /* Same line, same place, nothing to open: it keeps the geometry and drops the affordance. */
  .c2-turn-settled { cursor: default; }
  .c2-turn-settled:hover { color: var(--text-faintest); background: transparent; }
  .c2-turn-chevron.is-absent { visibility: hidden; }
  .c2-turn-count { color: var(--text-faintest); }
  .c2-turn-chevron {
    display: inline-block;
    transition: transform var(--motion-fast) var(--motion-ease);
  }
  .c2-turn-chevron.is-open { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) {
    .c2-alive-dots span { animation: none; }
    .c2-turn-chevron { transition: none; }
  }
</style>
