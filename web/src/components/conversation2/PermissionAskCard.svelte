<script lang="ts">
  /** The ask, as the thing standing between the agent and the rest of its turn.
   *
   * It takes the composer over rather than sitting beside it, because there is nothing
   * else to do here until it is answered. The buttons run left to right in the order of
   * how much they commit to, and the leftmost one — cancelling the turn — is the answer
   * that always works, whatever the ask turned out to be.
   */
  import { askActions, askIsGeneric } from "../../lib/conversation2/composer";
  import type { PermissionAskOption } from "../../lib/conversation2/wire";

  let {
    ask,
    busy = false,
    note = null,
    onAnswer,
    onCancelTurn
  }: {
    ask: {
      askId: string;
      title: string;
      detail: string | null;
      options: readonly PermissionAskOption[];
    };
    busy?: boolean;
    note?: string | null;
    onAnswer: (optionId: string) => void;
    onCancelTurn: () => void;
  } = $props();

  let actions = $derived(askActions(ask));
  let generic = $derived(askIsGeneric(ask));
</script>

<section
  class="c2-ask"
  class:is-generic={generic}
  aria-label="Permission ask"
  data-conversation2-ask={ask.askId}
  data-conversation2-ask-generic={generic ? "true" : undefined}
>
  <div class="c2-ask-head">
    <span class="c2-ask-title">{ask.title}</span>
  </div>
  {#if generic}
    <p class="c2-ask-note" data-conversation2-ask-fallback>
      This ask did not offer the usual answers, so Panels offers these. Cancelling the turn
      always works.
    </p>
  {/if}
  {#if note}
    <p class="c2-ask-note" data-conversation2-ask-note>{note}</p>
  {/if}
  <div class="c2-ask-actions" role="group" aria-label="Answers">
    {#each actions as action (action.act === "cancel_turn" ? "cancel" : action.optionId)}
      {#if action.act === "cancel_turn"}
        <button
          type="button"
          class="c2-ask-cancel"
          data-conversation2-ask-action="cancel_turn"
          disabled={busy}
          onclick={onCancelTurn}
        >{action.label}</button>
      {:else}
        <button
          type="button"
          class="c2-ask-answer"
          class:is-decline={action.emphasis === "decline"}
          class:is-primary={action.emphasis === "primary"}
          class:is-supplied={!action.supplied}
          data-conversation2-ask-action={action.optionId}
          data-conversation2-ask-supplied={action.supplied ? "true" : "false"}
          title={action.supplied ? undefined : "Panels supplied this answer; the backend may not take it"}
          disabled={busy}
          onclick={() => onAnswer(action.optionId)}
        >{action.label}</button>
      {/if}
    {/each}
  </div>
</section>

<style>
  .c2-ask {
    min-width: 0;
    max-width: 100%;
    display: grid;
    gap: var(--space-3);
    padding: var(--space-3);
    border-radius: var(--radius-md);
    background: var(--accent-surface);
    color: var(--accent-text);
  }
  .c2-ask-head { display: flex; min-width: 0; align-items: baseline; gap: var(--space-2); }
  .c2-ask-title {
    min-width: 0;
    overflow-wrap: anywhere;
    font-family: var(--font-ui);
    font-size: var(--type-sm);
    color: var(--accent-text);
  }
  .c2-ask-note {
    margin: 0;
    color: var(--accent-bright);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    overflow-wrap: anywhere;
  }
  .c2-ask-actions {
    display: flex;
    min-width: 0;
    max-width: 100%;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
  }
  button { max-width: 100%; overflow-wrap: anywhere; font: inherit; cursor: pointer; }
  button:disabled { cursor: default; opacity: 0.7; }
  .c2-ask-cancel {
    background: transparent;
    border: 0;
    border-radius: var(--radius-sm);
    color: var(--text-faint);
    padding: var(--space-2) 0;
  }
  .c2-ask-cancel:hover,
  .c2-ask-cancel:focus-visible { color: var(--accent-error); }
  .c2-ask-answer {
    background: transparent;
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    color: var(--accent-text);
    padding: var(--space-2) var(--space-3);
  }
  .c2-ask-answer:hover,
  .c2-ask-answer:focus-visible { border-color: var(--accent-bright); }
  .c2-ask-answer.is-decline { border: 0; color: var(--text-faint); padding: var(--space-2) 0; }
  .c2-ask-answer.is-decline:hover,
  .c2-ask-answer.is-decline:focus-visible { color: var(--accent-error); }
  .c2-ask-answer.is-primary {
    margin-inline-start: auto;
    background: var(--accent-bright);
    border-color: var(--accent-bright);
    color: var(--accent-ink);
    font-weight: 600;
    padding: var(--space-2) var(--space-4);
  }
  .c2-ask-answer.is-primary:hover { filter: brightness(1.08); }
  .c2-ask-answer.is-supplied { border-style: dashed; }
</style>
