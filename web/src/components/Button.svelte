<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    variant = "quiet",
    href,
    disabled = false,
    onclick,
    class: extraClass = "",
    children,
    ...rest
  }: {
    variant?: "primary" | "quiet" | "pill";
    href?: string;
    disabled?: boolean;
    onclick?: (event: MouseEvent) => void;
    class?: string;
    children?: Snippet;
    [key: string]: unknown;
  } = $props();

  let buttonClass = $derived(
    `button button--${variant}${extraClass ? ` ${extraClass}` : ""}`
  );
</script>

{#if href !== undefined}
  <a class={buttonClass} {href} {...rest}>{@render children?.()}</a>
{:else}
  <button type="button" class={buttonClass} {disabled} {onclick} {...rest}>
    {@render children?.()}
  </button>
{/if}
