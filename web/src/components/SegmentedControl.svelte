<script lang="ts">
  import type { Snippet } from "svelte";

  type Option = { value: string | null; label: string };

  let {
    name,
    options,
    optionContent,
    value = $bindable<string | null>(null)
  }: {
    name: string;
    options: Option[];
    optionContent?: Snippet<[Option]>;
    value?: string | null;
  } = $props();
</script>

<div class="seg" role="group" data-seg={name}>
  {#each options as option}
    <button
      type="button"
      class="opt"
      data-value={option.value === null ? "" : option.value}
      aria-pressed={option.value === value}
      onclick={() => (value = option.value)}
    >
      {#if optionContent}{@render optionContent(option)}{:else}{option.label}{/if}
    </button>
  {/each}
</div>
