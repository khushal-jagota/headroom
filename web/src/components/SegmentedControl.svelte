<script lang="ts">
  import type { Snippet } from "svelte";

  /* THE segmented control (Direction v2, Outcome 5): 28px segments, radius 8, no track; the
   * selected segment carries the 12% selected fill. The look lives in app.css, "Buttons"
   * (.segmented). The older .seg/.opt names stay for screens that still scope them. */
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

<div class="segmented seg" role="group" data-seg={name}>
  {#each options as option}
    <button
      type="button"
      class="segmented-option opt"
      data-value={option.value === null ? "" : option.value}
      aria-pressed={option.value === value}
      onclick={() => (value = option.value)}
    >
      {#if optionContent}{@render optionContent(option)}{:else}{option.label}{/if}
    </button>
  {/each}
</div>
