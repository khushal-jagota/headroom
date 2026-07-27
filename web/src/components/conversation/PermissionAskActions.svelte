<script lang="ts">
  /** The answers, in the composer's own footer where its buttons already live.
   *
   * They run left to right in the order of how much they commit to, and the leftmost —
   * cancelling the turn — is the answer that always works whatever the ask turned out to
   * be. A question's choices are not here: picking one is not a commitment to grade, so
   * they live in the band above with their numbers, and only the escape hatch remains.
   */
  import { askActions, askShape } from "../../lib/conversation/composer";
  import type { PermissionAskOption } from "../../lib/conversation/wire";

  let {
    ask,
    busy = false,
    onAnswer,
    onCancelTurn
  }: {
    ask: { options?: readonly PermissionAskOption[] } | null;
    busy?: boolean;
    onAnswer: (optionId: string) => void;
    onCancelTurn: () => void;
  } = $props();

  let shape = $derived(askShape(ask));
  let actions = $derived(shape === "question" ? askActions(null).slice(0, 1) : askActions(ask));
</script>

<div class="c2-ask-actions" role="group" aria-label="Answers">
  {#each actions as action (action.act === "cancel_turn" ? "cancel" : action.optionId)}
    {#if action.act === "cancel_turn"}
      <button
        type="button"
        class="c2-ask-cancel"
        data-conversation-ask-action="cancel_turn"
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
        data-conversation-ask-action={action.optionId}
        data-conversation-ask-supplied={action.supplied ? "true" : "false"}
        title={action.supplied ? undefined : "Panels supplied this answer; the backend may not take it"}
        disabled={busy}
        onclick={() => onAnswer(action.optionId)}
      >{action.label}</button>
    {/if}
  {/each}
</div>

<style>
  .c2-ask-actions {
    display: flex;
    min-width: 0;
    max-width: 100%;
    flex: 1;
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
    font-size: var(--type-xs);
    padding: var(--space-1) 0;
  }
  .c2-ask-cancel:hover,
  .c2-ask-cancel:focus-visible { color: var(--accent-error); }
  .c2-ask-answer {
    background: transparent;
    border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-sm);
    color: var(--text-default);
    font-size: var(--type-xs);
    padding: var(--space-1) var(--space-3);
  }
  .c2-ask-answer:hover,
  .c2-ask-answer:focus-visible { border-color: var(--accent-bright); }
  .c2-ask-answer.is-decline {
    border-color: transparent;
    color: var(--text-faint);
    padding: var(--space-1) var(--space-2);
  }
  .c2-ask-answer.is-decline:hover,
  .c2-ask-answer.is-decline:focus-visible { color: var(--accent-error); border-color: transparent; }
  .c2-ask-answer.is-primary {
    margin-inline-start: auto;
    background: var(--accent-bright);
    border-color: var(--accent-bright);
    color: var(--accent-ink);
    font-weight: 600;
  }
  .c2-ask-answer.is-primary:hover { filter: brightness(1.08); }
  .c2-ask-answer.is-supplied { border-style: dashed; }
</style>
