<script lang="ts">
  import type { Snippet } from "svelte";
  import { deckItemIn, deckItemOut, type Deck } from "../lib/review/deckMotion";

  /** The one thing on the review screen: an item, or the final screen. When `view` changes
   *  the old one leaves and the new one arrives. A change inside the same view moves nothing. */
  let { deck, view, final = false, element = $bindable(null), children }: {
    deck: Deck;
    view: string;
    final?: boolean;
    element?: HTMLElement | null;
    children: Snippet;
  } = $props();

  // Every change of view is a new appearance. A view that comes back while its last copy is
  // still leaving must be a new element: the framework would reuse the old one, mid-exit.
  // The key is worked out where it is read, so a change of view draws the new item once. A
  // counter written from an effect would draw it twice, and the first copy would leave at once.
  let appearance = 0;
  let lastView: string | null = null;
  const key = $derived.by(() => {
    if (lastView !== null && view !== lastView) appearance += 1;
    lastView = view;
    return `${view}:${appearance}`;
  });
</script>

{#key key}
  <div class="deck-item" class:deck-item--final={final} data-deck-item bind:this={element}
    in:deckItemIn={{ deck }} out:deckItemOut={{ deck }}>
    {@render children()}
  </div>
{/key}

<style>
  /* The final screen is a narrower column than a card. */
  .deck-item--final { width:100%; max-width:calc(var(--measure-index) + var(--page-gutter)*2); margin-inline:auto; }
</style>
