<script lang="ts">
  /** One conversation plan, presented either in the rest line or in its own strip. */
  import type { ConversationTaskProgress } from "../../lib/conversation/taskProgress";

  let {
    progress,
    variant,
    running = progress.turnRunning,
    moving = running,
    composerGap = false
  }: {
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

  function statusMark(status: string): string {
    if (status === "completed") return "✓";
    if (status === "in_progress") return "›";
    return "○";
  }
</script>

{#snippet checklist()}
  <span class="task-pop" role="list" aria-label="Tasks" data-conversation-task-list>
    {#if variant === "strip"}
      <span class="task-pop-head" aria-hidden="true">Tasks · {progress.completedCount} of {total} done</span>
    {/if}
    {#each progress.entries as entry, index (index)}
      <span
        class={`task-plan-item task-plan-item--${statusClass(entry.status)}`}
        role="listitem"
        data-conversation-task-status={entry.status}
      >
        <span class="task-plan-mark" aria-hidden="true">{statusMark(entry.status)}</span>
        <span class="task-plan-text">{entry.text}</span>
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
  <div class="task-strip" role="group" class:has-composer-gap={composerGap} data-conversation-task-strip onpointerleave={releaseIfOutside}>
      <button
        type="button"
        class="task-pill"
        data-conversation-task-control
      onpointerenter={() => progress.onDemand?.()}
      onfocus={() => progress.onDemand?.()}
      onpointerleave={releaseIfOutside}
      onblur={releaseIfOutside}
        data-conversation-task-progress
        aria-label={`Task ${position} of ${total}${currentText ? `: ${currentText}` : ""}, ${progress.completedCount} complete`}
      >
        {#if moving}<span class="acp-spin" aria-hidden="true"></span>{/if}
        <!-- The step being worked on, not a count of finished ones: "3 / 5" read as three done.
             Its name follows, cut to the pill's measure; the list above has it whole. -->
        <span class="task-count">Task {position} of {total}</span>
        {#if currentText}<span class="task-sep" aria-hidden="true">·</span><span class="task-now" data-conversation-task-current>{currentText}</span>{/if}
      </button>
      {@render checklist()}
  </div>
{/if}

<style>
  .task-strip {
    flex: none;
    height: 34px;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .task-strip.has-composer-gap { margin-block-end: var(--space-2); }
  /* The raised plane every floating thing in Panels shares (--surface-raised and its
     --border-raised ring), in the UI face: it floats over the transcript, so it carries the
     popover's finish rather than a near-page fill that disappeared against the bar. */
  .task-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    min-height: 26px;
    box-sizing: border-box;
    border: var(--border-hairline) solid var(--border-raised);
    border-radius: var(--radius-pill);
    background: var(--surface-raised);
    color: var(--text-default);
    font-family: var(--font-ui);
    font-size: var(--type-xs);
    padding: 0 var(--space-3);
    cursor: default;
    font-variant-numeric: tabular-nums;
  }
  .task-pill:hover,
  .task-pill:focus-visible { color: var(--text-strong); }
  /* The pill never outgrows the composer's column: the name gives way, not the count. */
  .task-pill { max-width: min(100%, 36rem); min-width: 0; }
  .task-count, .task-sep { flex: none; color: var(--text-muted); }
  .task-sep { margin-inline: calc(var(--space-1) * -1); }
  .task-now {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--text-default);
  }
  .task-pill:hover .task-now,
  .task-pill:focus-visible .task-now { color: var(--text-strong); }
  .c2-rest-progress {
    flex: none;
    position: relative;
    appearance: none;
    background: none;
    border: 0;
    padding: 0;
    margin: 0;
    color: var(--text-faint);
    font: inherit;
    font-variant-numeric: tabular-nums;
    cursor: default;
    border-radius: var(--radius-sm);
  }
  .c2-rest-progress:hover,
  .c2-rest-progress:focus-visible { color: var(--text-strong); }
  .c2-rest-progress:focus-visible {
    outline: var(--focus-width) solid var(--focus-ring);
    outline-offset: 2px;
  }
  .task-pop {
    position: absolute;
    bottom: calc(100% - var(--space-1));
    left: 50%;
    transform: translateX(-50%);
    min-width: 280px;
    max-width: 90vw;
    box-sizing: border-box;
    background: var(--surface-raised);
    border: var(--border-hairline) solid var(--border-raised);
    border-radius: var(--radius-lg);
    padding: var(--space-2) var(--space-3) var(--space-2);
    display: none;
    z-index: 5;
  }
  .task-pill:hover ~ .task-pop,
  .task-pill:focus ~ .task-pop,
  .task-pill:focus-visible ~ .task-pop,
  .c2-rest-progress:hover .task-pop,
  .c2-rest-progress:focus .task-pop,
  .c2-rest-progress:focus-visible .task-pop,
  .task-pop:hover {
    display: grid;
    gap: var(--space-1);
  }
  .c2-rest-progress .task-pop { left: 0; transform: none; }
  .task-pop-head {
    padding: var(--space-1) 0 var(--space-1);
    color: var(--text-muted);
    font-family: var(--font-ui);
    font-size: var(--type-xs);
    font-variant-numeric: tabular-nums;
    text-align: left;
  }
  .task-plan-item {
    display: flex;
    gap: var(--space-2);
    padding: var(--space-1) 0;
    font-size: var(--type-sm);
    color: var(--text-muted);
    align-items: baseline;
    font-family: var(--font-ui);
    letter-spacing: normal;
    line-height: 1.45;
    text-align: left;
  }
  .task-plan-mark {
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    flex: none;
    width: 14px;
    text-align: center;
  }
  .task-plan-text { min-width: 0; overflow-wrap: anywhere; }
  /* Done is said by the green check; a strike through the words only made them read faint. */
  .task-plan-item--done { color: var(--text-muted); }
  .task-plan-item--done .task-plan-mark { color: var(--accent-done); }
  .task-plan-item--now { color: var(--text-strong); }
  .task-plan-item--now .task-plan-mark { color: var(--accent-bright); }
  :global(.task-pill .acp-spin) { flex: none; margin-left: 0; }
</style>
