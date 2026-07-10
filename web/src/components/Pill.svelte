<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    keyLabel = "",
    class: extraClass = "",
    children,
    ...rest
  }: {
    keyLabel?: string;
    class?: string;
    children?: Snippet;
    [key: string]: unknown;
  } = $props();
</script>

<span class={`pill${extraClass ? ` ${extraClass}` : ""}`} {...rest}>{#if keyLabel}<span class="pill-key">{keyLabel}</span>{/if}{@render children?.()}</span>
