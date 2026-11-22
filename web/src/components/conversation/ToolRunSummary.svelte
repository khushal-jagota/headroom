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
  // v4: no step counts. The line says only a failure, in words, beside its sentence.
  let label = $derived(summary.failedCount > 0 ? `${summary.failedCount} failed` : "");
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
  <!-- v6: while a call runs, the row is the live tool: a spinner in the glyph's place,
       "Running", the command (mono, one line, ellipsis), the chevron right after it. No time on it: the turn's
       head carries the turn's time. -->
  <!-- p13b: one live indicator in the transcript, the Working line's spinner. A running row
       keeps the run's static icon; "Running" in #c4c4c4 500 (v14 (6)) says it is live. -->
  <!-- v9 (1): the Working line holds the one spinner in every view. -->
  <!-- v17 (3): a running row keeps the same quiet icon as a settled row; only its words are lit. -->
  <svg class="c2-run-icon" viewBox="0 0 24 24" aria-hidden="true" data-conversation-run-icon={live ? "running" : undefined}>
    {#each summary.iconPaths as path (path)}<path d={path} />{/each}
  </svg>
  <!-- v4.2: the chevron always ends the row's words: "Ran 2 commands ›", and while a call
       runs "Running <command> ›", the command in mono ellipsizing before the chevron. -->
  <span class="c2-run-line" title={live ? summary.running?.sentence : summary.sentence}>
    {#if live && summary.running}
      <span class="c2-run-verb">{summary.running.title}</span>{#if summary.running.summary}<span class="c2-run-subject" class:c2-run-subject--literal={summary.running.summaryIsLiteral}>{summary.running.summary}</span>{/if}
    {:else}
      <span class="c2-run-verb">{summary.sentence}</span>
    {/if}
    {#if label}<span class="c2-run-status" class:c2-run-status--failed={summary.failedCount > 0} data-conversation-work-status><span class="c2-run-dot" aria-hidden="true">·</span>{label}</span>{/if}
  </span>
  <svg class="c2-run-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 4 4 4-4 4" /></svg>
</button>

<style>
  /* A run of calls, folded: one status line, the glyph hanging in the mark gutter, the
     sentence ("Ran 2 commands") and its chevron right after it, as wide as what it says, as the
     turn's own fold line is. Live, the row stays one line: "Running" in the ink, its command a
     step quieter and ellipsized before the chevron. Only the Working line has a spinner. */
  .c2-run-summary {
    display: flex;
    align-items: center;
    column-gap: var(--cv-s2);
    justify-self: start;
    width: fit-content;
    max-width: calc(100% + var(--cv-mark-hang) + var(--cv-s2) * 2);
    min-width: 0;
    height: 32px;
    margin-inline: calc(-1 * (var(--cv-mark-hang) + var(--cv-s2))) calc(var(--cv-s2) * -1);
    padding: 0 var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-pill);
    background: transparent;
    color: #c4c4c4;
    font-family: var(--cv-font);
    font-size: var(--cv-type-control);
    line-height: var(--cv-leading-control);
    text-align: start;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-run-summary:hover { background-color: var(--cv-control); }
  .c2-run-summary > svg { flex: none; }
  .c2-run-summary:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: calc(var(--cv-focus-width) * -1);
  }
  .c2-run-icon {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--cv-quiet-ink);
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  /* One line, always: the subject gives way with an ellipsis inside the column. */
  .c2-run-line {
    flex: 0 1 auto;
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
    color: #c4c4c4;
    font-weight: var(--cv-weight-body);
  }
  .c2-run-summary.is-live .c2-run-verb { margin-inline-end: 2px; color: var(--cv-ink); font-weight: var(--cv-weight-label); }
  .c2-run-summary.is-live .c2-run-subject { color: #c4c4c4; }
  .c2-run-subject {
    flex: 0 1 auto;
    white-space: nowrap;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--cv-t3);
  }
  /* Code in the line is set at the line's own size, so it reads as one line, not a smaller one. */
  .c2-run-subject--literal { font-family: var(--cv-mono); font-size: var(--cv-type-control); }
  .c2-run-status {
    display: inline-flex;
    gap: 6px;
    min-width: max-content;
    color: var(--cv-t3);
    font-size: var(--cv-type-caption);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    text-align: end;
  }
  .c2-run-status--failed { color: var(--cv-danger); font-weight: var(--cv-weight-label); }
  .c2-run-chevron {
    width: 16px;
    height: 16px;
    fill: none;
    stroke: var(--cv-quiet-ink);
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
    transition: transform var(--cv-dur-expand) var(--cv-ease-out), stroke var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-run-summary:hover .c2-run-verb, .c2-run-summary:focus-visible .c2-run-verb { color: var(--cv-ink); }
  .c2-run-summary:hover .c2-run-chevron, .c2-run-summary:focus-visible .c2-run-chevron { stroke: var(--cv-ink); }
  .c2-run-summary.is-open .c2-run-chevron { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) {
    .c2-run-summary, .c2-run-chevron { transition: none; }
  }
</style>
