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

  let visible = $derived(
    progress.entries.length > 0 &&
      (variant === "rest"
        ? running && progress.currentEntry !== null
        : running && progress.hasUnfinishedEntry)
  );
  let position = $derived(
    progress.currentPosition ??
      progress.entries.findIndex((entry) => entry.status !== "completed") + 1
  );

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
      data-conversation-task-progress
      aria-label={`${progress.completedCount} of ${progress.entries.length} tasks complete`}
    >
      {position}/{progress.entries.length}
      {@render checklist()}
    </button>
  {/if}
{:else}
  <div class="task-strip" class:has-composer-gap={composerGap} data-conversation-task-strip>
    {#if visible}
      <button
        type="button"
        class="task-pill"
        data-conversation-task-control
        data-conversation-task-progress
        aria-label={`${progress.completedCount} of ${progress.entries.length} tasks complete`}
      >
        {#if moving}<span class="acp-spin" aria-hidden="true"></span>{/if}
        <span class="task-count">{position} / {progress.entries.length} tasks</span>
      </button>
      {@render checklist()}
    {/if}
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
  .task-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    background: var(--surface-2);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    padding: var(--space-1) var(--space-3);
    cursor: default;
    font-variant-numeric: tabular-nums;
  }
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
    outline: var(--border-hairline) solid var(--accent-bright);
    outline-offset: 2px;
  }
  .task-pop {
    position: absolute;
    bottom: calc(100% - var(--space-1));
    left: 50%;
    transform: translateX(-50%);
    min-width: 280px;
    max-width: 90vw;
    background: var(--surface-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-md);
    padding: var(--space-2) var(--space-3);
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
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    flex: none;
    width: 14px;
  }
  .task-plan-text { min-width: 0; overflow-wrap: anywhere; }
  .task-plan-item--done {
    color: var(--text-faint);
    text-decoration: line-through;
    text-decoration-color: var(--text-faintest);
  }
  .task-plan-item--done .task-plan-mark { color: var(--accent-done); text-decoration: none; }
  .task-plan-item--now { color: var(--text-strong); }
  .task-plan-item--now .task-plan-mark { color: var(--accent-bright); }
  :global(.task-pill .acp-spin) { flex: none; margin-left: 0; }
</style>
