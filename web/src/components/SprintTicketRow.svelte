<script lang="ts">
  import type { FieldStageVisualState } from "../lib/ui";
  import type { Priority } from "../lib/types";
  import PriorityTile from "./PriorityTile.svelte";
  import StageMark from "./StageMark.svelte";

  let {
    priority,
    title,
    state,
    ariaLabel,
    href = null,
    active = false,
    quiet = false,
    onclick = undefined,
    class: extraClass = "",
    stageMarkClass = "",
    stageMarkAttributes = {},
    ...rest
  }: {
    // No priority when the row's own container already answers for it: inside a
    // Sprint Item the tile is repeated noise.
    priority: Priority | null;
    title: string;
    state: FieldStageVisualState;
    ariaLabel: string;
    href?: string | null;
    active?: boolean;
    quiet?: boolean;
    onclick?: (() => void) | undefined;
    class?: string;
    stageMarkClass?: string;
    stageMarkAttributes?: Record<string, unknown>;
    [key: string]: unknown;
  } = $props();
</script>

{#snippet content()}
  {#if priority}<PriorityTile {priority} />{/if}
  <span class="ticket-row-title">{title}</span>
  <StageMark
    {state}
    class={stageMarkClass}
    aria-label={ariaLabel}
    {...stageMarkAttributes}
  />
{/snippet}

{#if href === null}
  <button
    type="button"
    class={`ticket-row${extraClass ? ` ${extraClass}` : ""}`}
    class:ticket-row--active={active}
    class:ticket-row--quiet={quiet}
    {onclick}
    {...rest}
  >
    {@render content()}
  </button>
{:else}
  <a
    class={`ticket-row${extraClass ? ` ${extraClass}` : ""}`}
    class:ticket-row--active={active}
    class:ticket-row--quiet={quiet}
    {href}
    {...rest}
  >
    {@render content()}
  </a>
{/if}
