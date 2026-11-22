<script lang="ts">
  /** One conversation plan, presented either in the rest line or in its own strip. */
  import type { ConversationTaskProgress } from "../../lib/conversation/taskProgress";
  import { codeSpans } from "../../lib/conversation/codeSpans";

  let {
    progress,
    variant,
    running = progress.turnRunning,
    moving = running,
    placement = "above",
    composerGap: _composerGap = false
  }: {
    /** Where the plan opens: over the pill in the composer stack, or under it in the thread. */
    placement?: "above" | "below";
    progress: ConversationTaskProgress;
    variant: "rest" | "strip";
    running?: boolean;
    moving?: boolean;
    composerGap?: boolean;
  } = $props();

  let total = $derived(progress.totalCount ?? progress.entries.length);
  let visible = $derived(
    total > 0 &&
      (variant === "rest"
        ? running && progress.currentEntry !== null
        : running && progress.hasUnfinishedEntry)
  );
  // The step in hand, by name: the count alone hid what is being done until hovered.
  let currentText = $derived(
    (progress.currentEntry ?? progress.entries.find((entry) => entry.status === "in_progress") ?? null)?.text.trim() || null
  );
  let position = $derived(
    progress.currentPosition ??
      progress.entries.findIndex((entry) => entry.status !== "completed") + 1
  );

  function releaseIfOutside(event: FocusEvent | PointerEvent): void {
    const container = (event.currentTarget as HTMLElement).closest('[data-conversation-task-strip]') ?? event.currentTarget as HTMLElement;
    if (event.relatedTarget instanceof Node && container.contains(event.relatedTarget)) return;
    if (container.matches(':hover') || container.contains(document.activeElement)) return;
    progress.onRelease?.();
  }

  function statusClass(status: string): string {
    if (status === "completed") return "done";
    if (status === "in_progress") return "now";
    return "pending";
  }

  /** Past this many steps the drawn mark would outgrow the pill; the words carry the count. */
  const SEGMENT_LIMIT = 12;
</script>

{#snippet words(text: string)}{#each codeSpans(text) as span, index (index)}{#if span.code}<code class="cv-code">{span.text}</code>{:else}{span.text}{/if}{/each}{/snippet}

{#snippet checklist()}
  <span class="task-pop" role="list" aria-label="Tasks" data-conversation-task-list>
    {#if variant === "strip"}
      <span class="task-pop-head" aria-hidden="true">Steps · {progress.completedCount} of {total} done</span>
    {/if}
    {#each progress.entries as entry, index (index)}
      <span
        class={`task-plan-item task-plan-item--${statusClass(entry.status)}`}
        role="listitem"
        data-conversation-task-status={entry.status}
      >
        <span class="task-plan-mark" aria-hidden="true">
          {#if entry.status === "completed"}
            <svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
          {:else if entry.status === "in_progress"}
            {#if moving}<span class="task-spin"></span>{:else}<span class="task-ring now"></span>{/if}
          {:else}
            <span class="task-ring"></span>
          {/if}
        </span>
        <span class="task-plan-text">{@render words(entry.text)}</span>
        <span class="cv-visually-hidden">{entry.status === "completed" ? ", done" : entry.status === "in_progress" ? ", in progress" : ""}</span>
      </span>
    {/each}
  </span>
{/snippet}

{#if variant === "rest"}
  {#if visible}
    <button
      type="button"
      class="c2-rest-progress"
      data-conversation-task-control
      onpointerenter={() => progress.onDemand?.()}
      onfocus={() => progress.onDemand?.()}
      onpointerleave={releaseIfOutside}
      onblur={releaseIfOutside}
      data-conversation-task-progress
      aria-label={`${progress.completedCount} of ${total} tasks complete`}
    >
      {position}/{total}
      {@render checklist()}
    </button>
  {/if}
{:else if visible}
  <div class="task-strip" class:opens-below={placement === "below"} role="group" data-conversation-task-strip onpointerleave={releaseIfOutside}>
    <button
      type="button"
      class="task-pill"
      data-conversation-task-control
      onpointerenter={() => progress.onDemand?.()}
      onfocus={() => progress.onDemand?.()}
      onpointerleave={releaseIfOutside}
      onblur={releaseIfOutside}
      data-conversation-task-progress
      aria-label={`Step ${position} of ${total}${currentText ? `: ${currentText}` : ""}, ${progress.completedCount} complete`}
    >
      <!-- Where the plan stands, drawn: one small square per step, the done ones quiet, the one
           in hand bright, the rest barely there. A long plan keeps its count in words only. -->
      {#if total <= SEGMENT_LIMIT}
        <span class="task-segments" aria-hidden="true">
          {#each Array.from({ length: total }, (_, index) => index) as index (index)}
            <span class="task-segment" class:done={index < position - 1} class:now={index === position - 1}></span>
          {/each}
        </span>
      {/if}
      <span class="task-count">Step {position} of {total}</span>
      {#if currentText}<span class="task-now" data-conversation-task-current>{@render words(currentText)}</span>{/if}
    </button>
    {@render checklist()}
  </div>
{/if}

<style>
  /* The pill stands at the left of the status slot over the composer, on the raised plane with
     no edge: the plan's mark, then "Task 3 of 5" quiet and the step in hand a step brighter. */
  .task-strip {
    flex: none;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    min-width: 0;
    height: 28px;
  }
  .task-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--cv-s2);
    height: 28px;
    max-width: min(100%, 36rem);
    min-width: 0;
    padding: 0 var(--cv-s3) 0 10px;
    border: 0;
    border-radius: var(--cv-r-pill);
    background: var(--cv-raised);
    color: var(--cv-t2);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    white-space: nowrap;
    overflow: hidden;
    cursor: default;
    font-variant-numeric: tabular-nums;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .task-pill:hover,
  .task-pill:focus-visible { background: var(--cv-control); }
  .task-pill:focus-visible,
  .c2-rest-progress:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: var(--cv-focus-offset);
  }
  /* Done steps are small filled squares; the step in hand is a longer bar that breathes, so
     the mark counts the same as the words: two done, one in hand, the rest to come. */
  .task-segments { flex: none; display: inline-flex; align-items: center; gap: 3px; }
  .task-segment { width: 6px; height: 6px; border-radius: 2px; background: rgb(255 255 255 / 16%); }
  .task-segment.done { background: var(--cv-t3); }
  .task-segment.now {
    width: 14px;
    background: var(--cv-t1);
    animation: task-segment-breathe 2s ease-in-out infinite;
  }
  @keyframes task-segment-breathe { 50% { opacity: 0.55; } }
  .task-count { flex: none; color: var(--cv-t3); }
  .task-now { min-width: 0; overflow: hidden; text-overflow: ellipsis; color: var(--cv-t2); }
  .task-pill:hover .task-now,
  .task-pill:focus-visible .task-now { color: var(--cv-t1); }
  .c2-rest-progress {
    flex: none;
    position: relative;
    appearance: none;
    background: none;
    border: 0;
    padding: 0;
    margin: 0;
    color: var(--cv-t3);
    font: inherit;
    font-variant-numeric: tabular-nums;
    cursor: default;
    border-radius: var(--cv-r-sm);
  }
  .c2-rest-progress:hover,
  .c2-rest-progress:focus-visible { color: var(--cv-t1); }
  /* The list opens upward from the pill's left edge, 8px over it, on the floating plane. It
     overlays the transcript and never moves the stack. */
  .task-pop {
    position: absolute;
    bottom: calc(100% + var(--cv-s2));
    left: 0;
    width: 340px;
    max-width: calc(100vw - var(--cv-s6));
    max-height: 60vh;
    overflow-y: auto;
    box-sizing: border-box;
    background: var(--cv-float);
    border-radius: var(--cv-r-lg);
    padding: 6px;
    display: none;
    z-index: 30;
    font-family: var(--cv-font);
  }
  /* The pointer travels from the pill to the list across the 8px between them. */
  .task-pop::after { content: ""; position: absolute; inset: 100% 0 auto; height: var(--cv-s2); }
  .opens-below .task-pop { bottom: auto; top: calc(100% + var(--cv-s2)); }
  .opens-below .task-pop::after { inset: auto 0 100%; }
  .task-pill:hover ~ .task-pop,
  .task-pill:focus ~ .task-pop,
  .task-pill:focus-visible ~ .task-pop,
  .c2-rest-progress:hover .task-pop,
  .c2-rest-progress:focus .task-pop,
  .c2-rest-progress:focus-visible .task-pop,
  .task-pop:hover {
    display: grid;
    animation: task-pop-in var(--cv-dur-base) var(--cv-ease-out);
  }
  @keyframes task-pop-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }
  .task-pop-head {
    padding: 6px var(--cv-s2);
    color: var(--cv-t3);
    font-size: var(--cv-type-caption);
    line-height: var(--cv-leading-caption);
    font-weight: var(--cv-weight-label);
    font-variant-numeric: tabular-nums;
    text-align: left;
  }
  .task-plan-item {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    min-height: 32px;
    padding: 6px var(--cv-s2);
    border-radius: var(--cv-r-md);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    color: var(--cv-t2);
    text-align: left;
    white-space: normal;
  }
  .task-plan-mark { flex: none; display: grid; place-items: center; width: 14px; height: 20px; }
  .task-plan-mark svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
  .task-ring { width: 12px; height: 12px; border-radius: 50%; box-shadow: inset 0 0 0 1.5px var(--cv-t4); }
  .task-ring.now { box-shadow: inset 0 0 0 1.5px var(--cv-t1); }
  .task-spin {
    width: 12px; height: 12px; border-radius: 50%;
    border: 1.6px solid rgb(255 255 255 / 16%); border-top-color: var(--cv-t1);
    animation: task-spin var(--cv-spin) linear infinite;
  }
  @keyframes task-spin { to { transform: rotate(360deg); } }
  .task-plan-text { min-width: 0; overflow-wrap: anywhere; }
  /* Done is quiet; the step in hand is bright on the control fill; what is to come is plain. */
  .task-plan-item--done { color: var(--cv-t3); }
  .task-plan-item--done .task-plan-mark { color: var(--cv-t3); }
  .task-plan-item--now { color: var(--cv-t1); background: var(--cv-control); }
  .cv-visually-hidden {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }
  @media (prefers-reduced-motion: reduce) {
    .task-spin { animation: none; }
    .task-segment.now { animation: none; }
    .task-pill:hover ~ .task-pop, .task-pill:focus ~ .task-pop, .task-pop:hover { animation: none; }
  }
</style>
