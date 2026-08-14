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
    priority: Priority;
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
  <PriorityTile {priority} />
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
