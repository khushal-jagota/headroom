<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    href = "#",
    title = "",
    children
  }: {
    href?: string;
    title?: string;
    children?: Snippet;
  } = $props();
</script>

<a class="entity-row" {href}>
  <span class="entity-row-title">{title}</span>
  <span class="entity-row-chips">{@render children?.()}</span>
</a>
