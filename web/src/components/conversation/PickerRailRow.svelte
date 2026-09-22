<script lang="ts">
  import type { Snippet } from "svelte";

  // One part in a rail. `on` is the part whose choices are showing, `held` is a part that
  // is selected but not showing, and `dim` is a part that cannot be chosen right now.
  // The row is a panel action, so the list's ArrowLeft reaches it and the picker keeps
  // its focus when the row is pressed.
  let {
    on = false,
    held = false,
    dim = false,
    disabled = false,
    attributes = {},
    onclick,
    icon,
    label,
    trailing
  }: {
    on?: boolean;
    held?: boolean;
    dim?: boolean;
    disabled?: boolean;
    attributes?: Record<string, string | undefined>;
    onclick: () => void;
    icon?: Snippet;
    label: string;
    trailing?: Snippet;
  } = $props();
</script>

<button
  type="button"
  class="picker-rail-row"
  class:on
  class:held
  class:dim
  {disabled}
  tabindex="-1"
  data-listbox-picker-action
  onmousedown={(event) => event.preventDefault()}
  {onclick}
  {...attributes}
>
  {#if icon}{@render icon()}{/if}
  <span class="picker-rail-row-label">{label}</span>
  {#if trailing}{@render trailing()}{/if}
</button>

<style>
  .picker-rail-row {
    display: flex; align-items: center; gap: var(--space-2); width: 100%; background: transparent;
    border: 0; color: var(--text-faint); cursor: pointer; font-family: var(--font-mono);
    font-size: var(--type-xs); padding: var(--space-2) var(--space-3); text-align: left; white-space: nowrap;
  }
  .picker-rail-row-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  .picker-rail-row:hover { background-image: var(--interaction-hover); color: var(--text-muted); }
  .picker-rail-row.held { color: var(--text-strong); }
  .picker-rail-row.on { background: var(--surface-recessed); color: var(--text-strong); }
  .picker-rail-row.dim { color: var(--text-faintest); opacity: .45; }
  .picker-rail-row.dim:hover { background: transparent; color: var(--text-faintest); }
  /* A backend mark is the one coloured icon a row carries. An unavailable part greys it. */
  .picker-rail-row.dim :global(.model-picker-mark:not(.hermes)) { filter: grayscale(1); }
  :global(.listbox-picker-panel[aria-busy="true"]) .picker-rail-row { cursor: progress; opacity: .55; }
</style>
