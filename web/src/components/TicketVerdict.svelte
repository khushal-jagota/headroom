<script lang="ts">
  import ErrorLine from "./ErrorLine.svelte";
  import type { TicketVerdict as Verdict } from "../lib/types";

  const ratings = [
    { value: 1, label: "Awful" },
    { value: 2, label: "Poor" },
    { value: 3, label: "Good" },
    { value: 4, label: "Great" },
    { value: 5, label: "Extraordinary" }
  ] as const;

  let {
    stage,
    verdict,
    onSave
  }: {
    stage: string;
    verdict: Verdict | null;
    onSave: (verdict: Verdict) => Promise<unknown>;
  } = $props();

  let editing = $state(false);
  let rating = $state<number | null>(null);
  let text = $state("");
  let inFlight = $state(false);
  let error = $state<unknown>(null);

  let editable = $derived(stage === "done");
  let ratingLabel = $derived(
    ratings.find((choice) => choice.value === verdict?.rating)?.label ?? null
  );

  function openEditor(): void {
    rating = verdict?.rating ?? null;
    text = verdict?.text ?? "";
    error = null;
    editing = true;
  }

  async function save(next: Verdict): Promise<void> {
    inFlight = true;
    error = null;
    try {
      await onSave(next);
      editing = false;
    } catch (err) {
      error = err;
    } finally {
      inFlight = false;
    }
  }
</script>

{#if verdict || editable}
  <section class="ticket-verdict" data-ticket-verdict data-editable={editable ? "true" : "false"}>
    {#if editing && editable}
      <div class="ticket-verdict-heading">Verdict</div>
      <fieldset class="ticket-verdict-rating-group">
        <legend>Rating</legend>
        <div class="ticket-verdict-ratings">
          {#each ratings as choice}
            <button
              type="button"
              class:ticket-verdict-rating-selected={rating === choice.value}
              data-verdict-rating={choice.value}
              aria-pressed={rating === choice.value}
              disabled={inFlight}
              onclick={() => (rating = rating === choice.value ? null : choice.value)}
            >{choice.label}</button>
          {/each}
        </div>
      </fieldset>
      <textarea
        bind:value={text}
        aria-label="Verdict text"
        placeholder="What did you like or dislike?"
        disabled={inFlight}
      ></textarea>
      <div class="ticket-verdict-actions">
        <button
          type="button"
          class="ticket-verdict-save"
          data-save-verdict
          disabled={inFlight}
          onclick={() => void save({ rating, text })}
        >Save</button>
        {#if verdict}
          <button
            type="button"
            disabled={inFlight}
            data-clear-verdict
            onclick={() => void save({ rating: null, text: null })}
          >Clear</button>
        {/if}
        <button type="button" disabled={inFlight} onclick={() => (editing = false)}>Cancel</button>
      </div>
      {#if error}<ErrorLine {error} />{/if}
    {:else if verdict}
      <div class="ticket-verdict-display">
        <div>
          <div class="ticket-verdict-heading">Verdict</div>
          {#if ratingLabel}<div class="ticket-verdict-rating-label">{ratingLabel}</div>{/if}
          {#if verdict.text}<p>{verdict.text}</p>{/if}
        </div>
        {#if editable}
          <button type="button" class="ticket-verdict-edit" data-edit-verdict onclick={openEditor}>Edit</button>
        {/if}
      </div>
    {:else}
      <button type="button" class="ticket-verdict-add" data-add-verdict onclick={openEditor}>+ Add verdict</button>
    {/if}
  </section>
{/if}

<style>
  .ticket-verdict {
    border-block: var(--border-hairline) solid var(--border-color);
    padding: var(--space-4) 0;
  }

  .ticket-verdict-display {
    display: flex;
    align-items: start;
    justify-content: space-between;
    gap: var(--space-4);
  }

  .ticket-verdict-heading {
    color: var(--text-muted);
    font-size: var(--type-xs);
    font-weight: 650;
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }

  .ticket-verdict-rating-label {
    margin-top: var(--space-1);
    font-family: var(--font-serif);
    font-size: var(--type-serif-xl);
  }

  .ticket-verdict p {
    margin: var(--space-2) 0 0;
    font-family: var(--font-serif);
    font-size: var(--type-serif-md);
    white-space: pre-wrap;
  }

  .ticket-verdict button,
  .ticket-verdict textarea {
    font: inherit;
  }

  .ticket-verdict button {
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    background: var(--surface-1);
    color: var(--text-default);
    cursor: pointer;
    padding: var(--space-2) var(--space-3);
  }

  .ticket-verdict button:disabled {
    cursor: default;
    opacity: 0.55;
  }

  .ticket-verdict-add,
  .ticket-verdict-edit {
    color: var(--text-muted) !important;
  }

  .ticket-verdict-ratings {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    margin-top: var(--space-2);
  }

  .ticket-verdict-rating-group {
    min-width: 0;
    margin: var(--space-3) 0 0;
    border: 0;
    padding: 0;
  }

  .ticket-verdict-rating-group legend {
    color: var(--text-muted);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-label);
    text-transform: uppercase;
  }

  .ticket-verdict-ratings .ticket-verdict-rating-selected {
    border-color: var(--accent-bright);
    background: var(--accent-surface);
    color: var(--accent-text);
  }

  .ticket-verdict textarea {
    box-sizing: border-box;
    width: 100%;
    min-height: calc(var(--space-7) * 2 + var(--space-4));
    margin-top: var(--space-3);
    resize: vertical;
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    background: var(--surface-1);
    color: var(--text-default);
    font-family: var(--font-serif);
    font-size: var(--type-serif-md);
    padding: var(--space-3);
  }

  .ticket-verdict-actions {
    display: flex;
    gap: var(--space-2);
    margin-top: var(--space-2);
  }

  .ticket-verdict-actions .ticket-verdict-save {
    border-color: var(--accent-bright);
    background: var(--accent-surface);
    color: var(--accent-text);
  }
</style>
