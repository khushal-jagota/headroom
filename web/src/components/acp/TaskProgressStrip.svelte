<script lang="ts">
  import type { ConversationActivity } from "../../lib/acp/contracts";
  import type { DeepReadonly, ReadonlySessionData } from "../../lib/acp/conversationState";

  let {
    plan,
    activity
  }: {
    plan: ReadonlySessionData["plan"];
    activity: DeepReadonly<ConversationActivity> | null;
  } = $props();

  // A turn is active while the worker is thinking, compacting, or blocked on a
  // permission decision — the only states in which the pill speaks.
  const ACTIVE_STATES = ["thinking", "working", "compacting", "waiting_for_permission"];
  // The spinner rides along only while work is actually moving, not while the
  // turn is parked waiting for the human.
  const SPINNER_STATES = ["thinking", "working", "compacting"];

  let activeTurn = $derived(activity !== null && ACTIVE_STATES.includes(activity.state));
  let hasActiveTask = $derived(
    plan.some((entry) => entry.status === "pending" || entry.status === "in_progress")
  );
  let pillVisible = $derived(activeTurn && hasActiveTask);
  let showSpinner = $derived(activity !== null && SPINNER_STATES.includes(activity.state));
  let doneCount = $derived(plan.filter((entry) => entry.status === "completed").length);

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

<div class="task-strip" data-acp-task-strip>
  {#if pillVisible}
    <button
      type="button"
      class="task-pill"
      aria-label={`${doneCount} of ${plan.length} tasks complete`}
    >
      {#if showSpinner}
        <span class="acp-spin" aria-hidden="true"></span>
      {/if}
      <span class="task-count">{doneCount} / {plan.length} tasks</span>
    </button>
    <div class="task-pop" role="list" aria-label="Tasks">
      {#each plan as entry, index (index)}
        <div class={`task-plan-item task-plan-item--${statusClass(entry.status)}`} role="listitem">
          <span class="task-plan-mark" aria-hidden="true">{statusMark(entry.status)}</span>
          <span class="task-plan-text">{entry.content}</span>
        </div>
      {/each}
    </div>
  {/if}
</div>

<style>
  /* In-flow strip between thread and composer; its 34px is reserved in every
     state so nothing floats over the thread. */
  .task-strip {
    flex: none;
    height: 34px;
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .task-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    background: var(--surface-raised);
    color: var(--text-muted);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    padding: var(--space-1) var(--space-3);
    cursor: default;
    font-variant-numeric: tabular-nums;
  }

  .task-pop {
    position: absolute;
    bottom: calc(100% - var(--space-1));
    left: 50%;
    transform: translateX(-50%);
    background: var(--surface-raised);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-md);
    padding: var(--space-2) var(--space-3);
    min-width: 280px;
    max-width: 90%;
    display: none;
    z-index: 5;
  }

  .task-pill:hover ~ .task-pop,
  .task-pill:focus ~ .task-pop,
  .task-pill:focus-visible ~ .task-pop,
  .task-pop:hover {
    display: grid;
    gap: var(--space-1);
  }

  .task-plan-item {
    display: flex;
    gap: var(--space-2);
    padding: var(--space-1) 0;
    font-size: var(--type-sm);
    color: var(--text-muted);
    align-items: baseline;
  }

  .task-plan-mark {
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    flex: none;
    width: 14px;
  }

  .task-plan-item--done {
    color: var(--text-faint);
    text-decoration: line-through;
    text-decoration-color: var(--text-faintest);
  }

  .task-plan-item--done .task-plan-mark {
    color: var(--accent-done);
    text-decoration: none;
  }

  .task-plan-item--now {
    color: var(--text-strong);
  }

  .task-plan-item--now .task-plan-mark {
    color: var(--accent-bright);
  }
</style>
