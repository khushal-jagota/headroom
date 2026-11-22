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
  <!-- v5/v5.1: while a call runs, the row is the live tool: a spinner in the glyph's place,
       "Running", the command (mono, at most 2 lines), the chevron. No time on it: the turn's
       head carries the turn's time. -->
  {#if live}
    <span class="c2-run-spin" role="img" aria-label="Running" data-conversation-run-spinner></span>
  {:else}
    <svg class="c2-run-icon" viewBox="0 0 24 24" aria-hidden="true">
      {#each summary.iconPaths as path (path)}<path d={path} />{/each}
    </svg>
  {/if}
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
  <svg class="c2-run-chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6" /></svg>
</button>

<style>
  /* Glyph, words (and a failure), chevron: as wide as what it says, so the chevron stands
     right after its label, as the turn's own fold line does. */
  .c2-run-summary {
    display: flex;
    align-items: center;
    column-gap: var(--cv-s2);
    justify-self: start;
    width: fit-content;
    max-width: calc(100% + var(--cv-s2) * 2);
    min-width: 0;
    height: 32px;
    margin-inline: calc(var(--cv-s2) * -1);
    padding: 0 var(--cv-s2);
    border: 0;
    border-radius: var(--cv-r-md);
    background: transparent;
    color: var(--cv-t3);
    font-family: var(--cv-font);
    font-size: var(--cv-type-ui);
    line-height: var(--cv-leading-ui);
    text-align: start;
    cursor: pointer;
    transition: background-color var(--cv-dur-fast) var(--cv-ease-out);
  }
  .c2-run-summary:hover { background-color: var(--cv-raised); }
  /* Running: the row is as wide as its words up to the column; a long command ellipsizes
     before the chevron, which stays at the row's end. */
  .c2-run-summary > svg { flex: none; }
  .c2-run-summary:focus-visible {
    outline: var(--cv-focus-width) solid var(--cv-accent);
    outline-offset: calc(var(--cv-focus-width) * -1);
  }
  /* v4.2: quieter than prose: the icon #8c8c8c, the label #a3a3a3. */
  .c2-run-icon {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: #8c8c8c;
    stroke-width: 1.6;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .c2-run-spin {
    flex: none;
    box-sizing: border-box;
    width: 12px;
    height: 12px;
    margin-inline: 1px;
    border: 1.5px solid rgb(255 255 255 / 18%);
    border-top-color: var(--cv-t2);
    border-radius: 50%;
    animation: c2-run-spin var(--cv-spin) linear infinite;
  }
  @keyframes c2-run-spin { to { transform: rotate(360deg); } }
  /* Live: the row grows to hold a command of up to two lines; the spinner and the chevron
     stand on the first line. */
  .c2-run-summary.is-live { height: auto; min-height: 32px; padding-block: 6px; align-items: flex-start; }
  .c2-run-summary.is-live > .c2-run-spin { margin-block-start: 4px; }
  .c2-run-summary.is-live > .c2-run-chevron { margin-block-start: 3px; }
  .c2-run-summary.is-live .c2-run-line {
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    white-space: normal;
    overflow: hidden;
    overflow-wrap: anywhere;
  }
  .c2-run-summary.is-live .c2-run-verb { margin-inline-end: 8px; }
  .c2-run-summary.is-live .c2-run-subject { white-space: normal; line-height: 20px; }
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
    /* v4.2: the label in #a3a3a3, a tone under the agent's #d9d9d9 prose. */
    color: var(--cv-t3);
    font-weight: var(--cv-weight-label);
  }
  .c2-run-subject {
    flex: 0 1 auto;
    white-space: nowrap;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--cv-t3);
  }
  /* Code in the line is set at the line's own size, so it reads as one line, not a smaller one. */
  .c2-run-subject--literal { font-family: var(--cv-mono); font-size: 13px; }
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
  .c2-run-summary:hover .c2-run-subject, .c2-run-summary:focus-visible .c2-run-subject { color: var(--cv-t3); }
  .c2-run-summary:hover .c2-run-chevron, .c2-run-summary:focus-visible .c2-run-chevron { stroke: var(--cv-t2); }
  .c2-run-summary.is-open .c2-run-chevron { transform: rotate(90deg); }
  @media (prefers-reduced-motion: reduce) {
    .c2-run-spin { animation-duration: 3s; }
    .c2-run-summary, .c2-run-chevron { transition: none; }
  }
</style>
