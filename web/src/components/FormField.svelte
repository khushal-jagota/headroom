<script lang="ts">
  import type { Snippet } from "svelte";

  let {
    label,
    optional = false,
    children
  }: {
    label: string;
    optional?: boolean;
    children?: Snippet;
  } = $props();
</script>

<div class="form-field">
  <div class="form-label">{label}{#if optional}<span class="form-label-opt"> — optional</span>{/if}</div>
  {@render children?.()}
</div>
