<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    variant = "content",
    defaultOpen = false,
    class: extraClass = "",
    title,
    summary,
    chevron = "trailing",
    children,
    ...rest
  }: {
    variant?: string;
    defaultOpen?: boolean;
    class?: string;
    title?: string;
    summary?: Snippet;
    chevron?: "trailing" | "leading" | "none";
    children?: Snippet;
    [key: string]: unknown;
  } = $props();
</script>

<details
  class={`disclosure disclosure--${variant}${extraClass ? ` ${extraClass}` : ""}`}
  open={defaultOpen}
  {...rest}
>
  <summary class="disclosure-summary">
    {#if chevron === "leading"}
      <span class="disclosure-chev" aria-hidden="true"></span>
    {/if}
    {#if summary}
      {@render summary()}
    {:else}
      <span class="disclosure-title">{title}</span>
    {/if}
    {#if chevron === "trailing"}
      <span class="disclosure-chev" aria-hidden="true"></span>
    {/if}
  </summary>
  <div class="disclosure-body">
    {@render children?.()}
  </div>
</details>
