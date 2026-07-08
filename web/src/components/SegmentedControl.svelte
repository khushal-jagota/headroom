<script lang="ts">
  type Option = { value: string | null; label: string };

  let {
    name,
    options,
    value = $bindable<string | null>(null)
  }: { name: string; options: Option[]; value?: string | null } = $props();
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
      {option.label}
    </button>
  {/each}
</div>
