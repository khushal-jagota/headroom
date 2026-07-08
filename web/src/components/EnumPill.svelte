<script lang="ts">
  type Option = { value: string; label: string };

  let {
    keyLabel = "",
    value = "",
    options = [],
    variant = "",
    onChange
  }: {
    keyLabel?: string;
    value?: string;
    options: Option[];
    variant?: string;
    onChange?: (value: string) => void;
  } = $props();

  let label = $derived(options.find((option) => option.value === value)?.label ?? value);
</script>

<span class={`pill${variant ? ` pill--${variant}` : ""}`}>
  {#if keyLabel}<span class="pill-key">{keyLabel}</span>{/if}
  {label}
  <select value={value} onchange={(event) => onChange?.(event.currentTarget.value)}>
    {#each options as option}
      <option value={option.value}>{option.label}</option>
    {/each}
  </select>
</span>
