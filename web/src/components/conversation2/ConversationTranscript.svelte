<script lang="ts">
  /** The thread: one line per finished thing, in the order it happened.
   *
   * The prompt is the only bubble, the agent is bubble-less prose, and a tool call is a
   * single quiet row that fills in its own mark when it finishes. Text still arriving is
   * drawn at the end and disappears the moment its finished row lands.
   */
  import MarkdownBlock from "../MarkdownBlock.svelte";
  import type { TranscriptRow } from "../../lib/conversation2/transcript";
  import {
    askDeadSentence,
    turnEndingSentence,
    TURN_STOPPED_SENTENCE
  } from "../../lib/conversation2/transcript";

  let { rows }: { rows: readonly TranscriptRow[] } = $props();

  function modeChip(mode: string): string | null {
    if (mode === "send_now") return "sent now";
    if (mode === "steer") return "steered";
    return null;
  }

  function runValuesLine(model: string | null, reasoningEffort: string | null): string {
    const parts = [model ?? "the backend's own model"];
    if (reasoningEffort) parts.push(reasoningEffort);
    return `now running on ${parts.join(" · ")}`;
  }

  function askStateLine(row: Extract<TranscriptRow, { kind: "permission_ask" }>): string {
    if (row.state === "answered") return `answered · ${row.answeredOptionLabel ?? ""}`;
    if (row.state === "dead") return askDeadSentence(row.deadReason);
    return "waiting for you";
  }
</script>

<div class="c2-transcript" data-conversation2-transcript>
  {#each rows as row (row.key)}
    {#if row.kind === "prompt"}
      <article class="chat-u" data-conversation2-row="prompt">
        <div class="c2-label">
          {row.senderLabel}{#if modeChip(row.mode)}<span class="c2-chip">{modeChip(row.mode)}</span>{/if}
        </div>
        {row.text}
      </article>
    {:else if row.kind === "prompt_refused"}
      <article class="chat-system c2-refused" data-conversation2-row="prompt_refused">
        <div class="c2-label">{row.senderLabel} · not delivered · {row.sentence}</div>
        {row.text}
      </article>
    {:else if row.kind === "prompt_discarded"}
      <article class="chat-system" data-conversation2-row="prompt_discarded">
        <div class="c2-label">{row.senderLabel} · discarded without being delivered</div>
        {row.text}
      </article>
    {:else if row.kind === "agent_message"}
      <article class="chat-a" data-conversation2-row="agent_message">
        <MarkdownBlock text={row.text} />
      </article>
    {:else if row.kind === "streaming_agent_message"}
      <article class="chat-a c2-streaming" data-conversation2-row="streaming">
        <MarkdownBlock text={row.text} />
      </article>
    {:else if row.kind === "tool_call"}
      <div class="acp-step" data-conversation2-row="tool_call" data-conversation2-tool={row.toolCallId}>
        <span class="acp-step-title">{row.title}</span>
        {#if row.status === "completed"}
          <span class="acp-step-mark acp-mark-ok" role="img" aria-label="Completed">✓</span>
        {:else if row.status === "failed"}
          <span class="acp-step-mark acp-mark-fail" role="img" aria-label="Failed">✕</span>
        {:else}
          <span class="acp-spin" role="img" aria-label="Running"></span>
        {/if}
        {#if row.progress ?? row.detail}
          <div class="c2-tool-detail">{row.progress ?? row.detail}</div>
        {/if}
      </div>
    {:else if row.kind === "permission_ask"}
      <div
        class="c2-ask-row"
        class:is-dead={row.state === "dead"}
        data-conversation2-row="permission_ask"
        data-conversation2-ask-state={row.state}
      >
        <div class="c2-label">permission · {askStateLine(row)}</div>
        <div class="c2-ask-title">{row.title}</div>
        {#if row.detail}<div class="c2-ask-detail">{row.detail}</div>{/if}
      </div>
    {:else if row.kind === "model_changed"}
      <div class="acp-compaction" role="separator" data-conversation2-row="model_changed">
        <span>{runValuesLine(row.model, row.reasoningEffort)}</span>
      </div>
    {:else if row.kind === "turn_ended" && row.ending === "failed"}
      <div class="acp-turn-end acp-turn-end--error" role="alert" data-conversation2-row="turn_ended">
        {turnEndingSentence(row.ending, row.errorSummary)}
      </div>
    {:else if row.kind === "turn_ended" && row.ending === "interrupted"}
      <div class="acp-turn-end" data-conversation2-row="turn_ended">
        {turnEndingSentence(row.ending, row.errorSummary)}
      </div>
    {:else if row.kind === "turn_stopped"}
      <div class="acp-turn-end" data-conversation2-row="turn_stopped">{TURN_STOPPED_SENTENCE}</div>
    {/if}
  {/each}
</div>

<style>
  .c2-transcript { display: contents; }
  .c2-label {
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    margin-block-end: var(--space-1);
  }
  .c2-chip {
    margin-inline-start: var(--space-2);
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-pill);
    color: var(--accent-bright);
    padding: 0 var(--space-2);
  }
  .c2-refused .c2-label { color: var(--accent-error); }
  .c2-streaming { color: var(--text-muted); }
  .c2-tool-detail {
    flex-basis: 100%;
    color: var(--text-faint);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    overflow-wrap: anywhere;
  }
  .c2-ask-row {
    align-self: stretch;
    border-inline-start: var(--border-hairline) solid var(--accent-bright);
    padding-inline-start: var(--space-3);
    color: var(--text-default);
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    overflow-wrap: anywhere;
  }
  .c2-ask-row.is-dead {
    border-inline-start-color: var(--text-faintest);
    color: var(--text-faintest);
  }
  .c2-ask-row.is-dead .c2-label { color: var(--text-faintest); }
  .c2-ask-title { color: var(--text-strong); }
  .c2-ask-row.is-dead .c2-ask-title { color: var(--text-faint); }
  .c2-ask-detail { color: var(--text-faint); font-family: var(--font-mono); font-size: var(--type-xs); }
</style>
