<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    label,
    labelContent,
    count,
    variant = "",
    class: extraClass = "",
    ...rest
  }: {
    label?: string;
    labelContent?: Snippet;
    count?: number;
    variant?: string;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

<div
  class={`section-heading${variant ? ` section-heading--${variant}` : ""}${
    extraClass ? ` ${extraClass}` : ""
  }`}
  {...rest}
>
  <span class="section-heading-label">
    {#if labelContent}{@render labelContent()}{:else}{label}{/if}
  </span>
  {#if count !== undefined}<span class="section-heading-count">· {count}</span>{/if}
</div>
