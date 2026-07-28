<script lang="ts">
  /** Which backend this conversation runs on, and — before there is one — which it will.
   *
   * A conversation is created on a backend and stays on it: there is a live process behind
   * it from its first message, and nothing here could move it to another. So the rail has
   * two states and they are different things. Before a conversation exists it is the
   * choice, and taking one is saying what the next message creates. Once one exists it is
   * a label that says why there is nothing to press, rather than a control that would
   * refuse.
   *
   * The set is the contract's own closed three rather than whatever the machine reported,
   * so a backend that is not installed is still a backend here. What one says about itself
   * is the backend card's business.
   */
  import { CONVERSATION_BACKEND_KEYS } from "../../lib/conversation/wire";
  import type { ConversationBackendKey } from "../../lib/conversation/wire";

  let {
    showing,
    locked = false,
    onChoose
  }: {
    /** The backend in force: what this conversation runs on, or what the next message
     *  would create one on. */
    showing: ConversationBackendKey | null;
    /** Whether there is a conversation, which is the whole of what fixes the backend. */
    locked?: boolean;
    onChoose: (key: ConversationBackendKey) => void;
  } = $props();
</script>

<div class="c2-rail" data-conversation-backend-rail role="group" aria-label="Backend">
  {#if locked}
    <span class="c2-rail-mark is-on" data-conversation-backend-locked={showing}>{showing}</span>
    <p class="c2-rail-why">A conversation cannot move to another backend.</p>
  {:else}
    <!-- Pressing one leaves the keyboard where it is, so the panel this sits in is not
         taken away by the press that is using it. -->
    {#each CONVERSATION_BACKEND_KEYS as key (key)}
      <button
        type="button"
        class="c2-rail-mark"
        class:is-on={key === showing}
        data-conversation-backend={key}
        data-conversation-backend-showing={key === showing ? "true" : undefined}
        aria-pressed={key === showing}
        onmousedown={(event) => event.preventDefault()}
        onclick={() => onChoose(key)}
      >{key}</button>
    {/each}
  {/if}
</div>

<style>
  .c2-rail { display: grid; align-content: start; gap: var(--space-1); }
  .c2-rail-mark {
    display: block;
    width: 100%;
    background: transparent;
    border: var(--border-hairline) solid transparent;
    border-radius: var(--radius-sm);
    color: var(--text-faintest);
    font-family: var(--font-mono);
    font-size: var(--type-xs);
    letter-spacing: var(--tracking-mono);
    line-height: 1.2;
    padding: var(--space-1) var(--space-2);
    text-align: left;
  }
  button.c2-rail-mark { cursor: pointer; }
  button.c2-rail-mark:hover { background: var(--surface-overlay); color: var(--text-muted); }
  .c2-rail-mark.is-on { background: var(--surface-sunken); color: var(--text-strong); }
  /* Why there is nothing to press. It is one line rather than a mark you can hover,
     because a control that has gone quiet with no reason given is the thing this is
     against. */
  .c2-rail-why {
    margin: 0;
    max-width: calc(var(--type-xs) * 11);
    color: var(--text-faintest);
    font-size: var(--type-xs);
    line-height: 1.4;
    padding: 0 var(--space-2);
  }
</style>
