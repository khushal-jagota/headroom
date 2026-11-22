<script lang="ts">
  /** A run of tool calls, closed: one quiet line that says what the run did.
   *
   * The work is the agent's, not the conversation's, so even Full keeps it one press
   * away. The line is the run's own sentence ("Ran 4 commands and read 2 files"); while a
   * call is running it says that call instead, in the present tense, beside the live mark.
   * A failure is never folded out of sight: the line says it in words, in the danger tone.
   * It has the tool row's own geometry, so a run opened under it reads as its continuation.
   */
  import type { ToolRunSummary } from "../../lib/conversation/toolCallPresentation";

  let {
    summary,
    callCount,
    open = false,
    controls = undefined,
    activityUncertain = false,
    onToggle
  }: {
    summary: ToolRunSummary;
    /** How many calls the run holds, which the closed line counts while one is running. */
    callCount: number;
    open?: boolean;
    /** The id of the region this line opens, when it is drawn. */
    controls?: string;
    activityUncertain?: boolean;
    onToggle: () => void;
  } = $props();

  let live = $derived(summary.running !== null && !activityUncertain);
  let label = $derived(
    summary.failedCount > 0 ? `${summary.failedCount} failed` : live ? `${callCount} ${callCount === 1 ? "step" : "steps"}` : ""
  );
</script>

<button
  type="button"
  class="c2-run-summary"
  class:is-open={open}
  class:is-live={live}
  data-conversation-work-fold
  data-conversation-work-summary
  data-conversation-work-failed={summary.failedCount > 0 ? "true" : undefined}
  aria-expanded={open}
  aria-controls={controls}
  onclick={onToggle}
>
  {#if live}
    <span class="c2-run-spin" role="img" aria-label="Running"></span>
  {:else}
    <svg class="c2-run-icon" viewBox="0 0 24 24" aria-hidden="true">
      {#each summary.iconPaths as path (path)}<path d={path} />{/each}
    </svg>
  {/if}
  <span class="c2-run-line" title={live ? summary.running?.sentence : summary.sentence}>
    {#if live && summary.running}
      <span class="c2-run-verb">{summary.running.title}</span>{#if summary.running.summary}<span class="c2-run-subject" class:c2-run-subject--literal={summary.running.summaryIsLiteral}>{summary.running.summary}</span>{/if}
    {:else}
      <span class="c2-run-verb">{summary.sentence}</span>
    {/if}
  </span>
  <span class="c2-run-status" class:c2-run-status--failed={summary.failedCount > 0} data-conversation-work-status>{label}</span>
  <svg class="c2-run-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
</button>

<style>
  /* The tool row's own grid: glyph, words, status column, chevron slot. */
  .c2-run-summary {
    display: grid;
    grid-template-columns: 14px minmax(0, 1fr) auto 14px;
    align-items: center;
    column-gap: 10px;
    width: calc(100% + var(--cv-s2) * 2);
    min-width: 0;
    height: 32px;
    margin-inline: calc(var(--cv-s2) * -1);
    padding: 0 var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-md);
    background: transparent;
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-meta);
    line-height: var(--cv-leading-meta);
    text-align: start;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-run-summary:hover { background-color: var(--cv-raised); }
  .c2-run-summary:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: calc(var(--cv-focus-width) * -1);
  }
  .c2-run-icon {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: var(--cv-t4);
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .c2-run-spin {
    width: 12px;
    height: 12px;
    margin-inline-start: 1px;
    border-radius: var(--cv-r-pill);
    border: 1.6px solid rgb(255 255 255 / 16%);
    border-top-color: var(--cv-t2);
    animation: c2-run-spin var(--cv-spin) linear infinite;
  }
  @keyframes c2-run-spin { to { transform: rotate(360deg); } }
  /* One line, always: the subject gives way with an ellipsis inside the column. */
  .c2-run-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
    overflow: hidden;
    white-space: nowrap;
  }
  .c2-run-verb {
    flex: none;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--cv-t2);
    font-weight: var(--cv-weight-label);
  }
  .c2-run-subject {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--cv-t3);
  }
  .c2-run-subject--literal { font-family: var(--cv-mono); font-size: var(--cv-type-code); }
  .c2-run-status {
    min-width: 0;
    color: var(--cv-t4);
    font-size: var(--cv-type-caption);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    text-align: end;
  }
  .c2-run-status--failed { color: var(--cv-danger); font-weight: var(--cv-weight-label); }
  .c2-run-chevron {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: var(--cv-t4);
    stroke-width: 1.8;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out), stroke var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-run-summary:hover .c2-run-verb, .c2-run-summary:focus-visible .c2-run-verb { color: var(--cv-t1); }
  .c2-run-summary:hover .c2-run-chevron, .c2-run-summary:focus-visible .c2-run-chevron { stroke: var(--cv-t2); }
  .c2-run-summary.is-open .c2-run-chevron { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) {
    .c2-run-spin { animation: none; }
    .c2-run-summary, .c2-run-chevron { transition: none; }
  }
</style>
