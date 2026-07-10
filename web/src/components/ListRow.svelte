<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    title = "",
    href,
    onclick,
    variant = "",
    active = false,
    class: extraClass = "",
    leading,
    trailing,
    ...rest
  }: {
    title?: string;
    href?: string;
    onclick?: (event: MouseEvent) => void;
    variant?: string;
    active?: boolean;
    class?: string;
    leading?: Snippet;
    trailing?: Snippet;
    [key: string]: unknown;
  } = $props();

  let rowClass = $derived(
    `list-row${variant ? ` list-row--${variant}` : ""}${active ? " active" : ""}${
      extraClass ? ` ${extraClass}` : ""
    }`
  );
</script>

{#if href !== undefined}
  <a class={rowClass} {href} {...rest}>
    {#if leading}{@render leading()}{/if}
    <span class="list-row-title">{title}</span>
    {#if trailing}{@render trailing()}{/if}
  </a>
{:else if onclick !== undefined}
  <button type="button" class={rowClass} {onclick} {...rest}>
    {#if leading}{@render leading()}{/if}
    <span class="list-row-title">{title}</span>
    {#if trailing}{@render trailing()}{/if}
  </button>
{:else}
  <div class={rowClass} {...rest}>
    {#if leading}{@render leading()}{/if}
    <span class="list-row-title">{title}</span>
    {#if trailing}{@render trailing()}{/if}
  </div>
{/if}
