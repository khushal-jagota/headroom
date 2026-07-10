<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    title = "",
    titleContent,
    meta,
    class: extraClass = "",
    ...rest
  }: {
    title?: string;
    titleContent?: Snippet;
    meta?: Snippet;
    class?: string;
    [key: string]: unknown;
  } = $props();
</script>

<header class={`screen-header${extraClass ? ` ${extraClass}` : ""}`} {...rest}>
  <div class="screen-header-row">
    <div class="screen-header-title">
      {#if titleContent}{@render titleContent()}{:else}{title}{/if}
    </div>
    {#if meta}<div class="screen-header-meta">{@render meta()}</div>{/if}
  </div>
</header>
