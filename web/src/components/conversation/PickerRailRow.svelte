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
    detail,
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
    detail?: string;
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
  <span class="picker-rail-row-copy">
    <span class="picker-rail-row-label">{label}</span>
    {#if detail}<span class="picker-rail-row-detail">{detail}</span>{/if}
  </span>
  {#if trailing}{@render trailing()}{/if}
</button>

<style>
  /* The rail speaks the interface face at the list's own size, so the two halves of the
   * panel read as one menu. A part that cannot be chosen now stays legible in the tertiary
   * tone (4.6:1 on the raised plane); only its mark is greyed. */
  .picker-rail-row {
    display: flex; align-items: center; gap: var(--space-2); width: 100%; min-height: 32px; background: transparent;
    border: 0; color: var(--text-muted); cursor: pointer; font-family: var(--font-ui);
    font-size: var(--type-sm); padding: var(--space-2) var(--space-3); text-align: left; white-space: nowrap;
  }
  .picker-rail-row-copy { display: flex; flex: 1; flex-direction: column; min-width: 0; }
  .picker-rail-row-label, .picker-rail-row-detail { overflow: hidden; text-overflow: ellipsis; }
  .picker-rail-row-detail { max-width: 110px; color: var(--tone-tertiary); font-size: var(--type-small); }
  .picker-rail-row:hover { background-image: var(--interaction-hover); color: var(--text-strong); }
  .picker-rail-row.held { color: var(--text-strong); }
  .picker-rail-row.on { background: var(--surface-selected); color: var(--text-strong); }
  .picker-rail-row.dim { color: var(--tone-tertiary); cursor: default; }
  .picker-rail-row.dim:hover { background-image: none; color: var(--tone-tertiary); }
  /* A backend mark is the one coloured icon a row carries. An unavailable part greys it. */
  .picker-rail-row.dim :global(.model-picker-mark) { filter: grayscale(1); opacity: .6; }
  :global(.listbox-picker-panel[aria-busy="true"]) .picker-rail-row { cursor: progress; opacity: .55; }
</style>
