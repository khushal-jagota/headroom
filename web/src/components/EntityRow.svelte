<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    href = "#",
    title = "",
    card = false,
    ticketId = "",
    itemId = "",
    children
  }: {
    href?: string;
    title?: string;
    card?: boolean;
    ticketId?: string;
    itemId?: string;
    children?: Snippet;
  } = $props();
</script>

<a
  class="entity-row"
  {href}
  data-card={card ? "" : undefined}
  data-ticket-id={ticketId || undefined}
  data-item-id={itemId || undefined}
>
  <span class="entity-row-title">{title}</span>
  <span class="entity-row-chips">{@render children?.()}</span>
</a>
