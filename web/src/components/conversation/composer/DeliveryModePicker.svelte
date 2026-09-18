<script lang="ts">
  import { tick } from "svelte";
  import type { PromptDeliveryMode } from "../../../lib/conversation/wire";

  let {
    value,
    disabled = false,
    onChoose
  }: {
    value: PromptDeliveryMode;
    disabled?: boolean;
    onChoose: (mode: PromptDeliveryMode) => void;
  } = $props();

  const choices: readonly { value: PromptDeliveryMode; name: string }[] = [
    { value: "steer", name: "Steer" },
    { value: "queue", name: "Queue" },
    { value: "send_now", name: "Send now" }
  ];
  const listId = $props.id();
  let open = $state(false);
  let activeIndex = $state(0);
  let root = $state<HTMLDivElement | null>(null);
  let trigger = $state<HTMLButtonElement | null>(null);
  let list = $state<HTMLDivElement | null>(null);

  let selected = $derived(choices.find((choice) => choice.value === value) ?? choices[0]);

  $effect(() => {
    if (!open) return;
    list?.focus();
  });

  $effect(() => {
    if (disabled) open = false;
  });

  $effect(() => {
    if (!open) return;
    function closeOutside(event: PointerEvent): void {
      if (event.target instanceof Node && root !== null && !root.contains(event.target)) {
        open = false;
      }
    }
    document.addEventListener("pointerdown", closeOutside, true);
    return () => document.removeEventListener("pointerdown", closeOutside, true);
  });

  function openPanel(): void {
    open = true;
    activeIndex = Math.max(0, choices.findIndex((choice) => choice.value === value));
  }

  function closeToTrigger(): void {
    open = false;
    trigger?.focus();
  }

  function choose(mode: PromptDeliveryMode): void {
    if (disabled) return;
    onChoose(mode);
    open = false;
    void tick().then(() => trigger?.focus());
  }

  function onTriggerKeydown(event: KeyboardEvent): void {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    openPanel();
  }

  function onListKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      closeToTrigger();
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : choices.length - 1;
      activeIndex = (activeIndex + step) % choices.length;
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      activeIndex = event.key === "Home" ? 0 : choices.length - 1;
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && choices[activeIndex] !== undefined) {
      event.preventDefault();
      choose(choices[activeIndex].value);
    }
  }
</script>

<div class="delivery-mode-picker" bind:this={root} data-conversation-send-mode>
  <button
    type="button"
    class="delivery-mode-trigger"
    class:is-open={open}
    bind:this={trigger}
    data-conversation-send-mode-trigger
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-label={`Message delivery mode: ${selected.name}`}
    {disabled}
    onkeydown={onTriggerKeydown}
    onclick={() => (open ? closeToTrigger() : openPanel())}
  >
    <span class="delivery-mode-face">{selected.name}</span>
    <span class="delivery-mode-chevron" aria-hidden="true">{open ? "⌃" : "⌄"}</span>
  </button>

  {#if open}
    <div
      class="delivery-mode-panel"
      bind:this={list}
      id={listId}
      data-conversation-send-mode-panel
      role="listbox"
      aria-label="Message delivery mode"
      aria-activedescendant={`${listId}-${activeIndex}`}
      tabindex="-1"
      onkeydown={onListKeydown}
      onfocusout={(event) => {
        if (!(event.relatedTarget instanceof Node) || !root?.contains(event.relatedTarget)) {
          open = false;
        }
      }}
    >
      {#each choices as choice, index (choice.value)}
        <button
          type="button"
          class="delivery-mode-choice"
          class:cursor={index === activeIndex}
          class:on={choice.value === value}
          id={`${listId}-${index}`}
          role="option"
          tabindex="-1"
          aria-selected={choice.value === value}
          data-conversation-send-mode-choice={choice.value}
          data-conversation-send-mode-active={index === activeIndex ? "true" : undefined}
          onmouseenter={() => (activeIndex = index)}
          onmousedown={(event) => event.preventDefault()}
          onclick={() => choose(choice.value)}
        >
          <span>{choice.name}</span>
          <span class="delivery-mode-tick" aria-hidden="true">{choice.value === value ? "✓" : ""}</span>
        </button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .delivery-mode-picker { position: relative; display: inline-flex; min-width: 0; }
  .delivery-mode-trigger {
    display: inline-flex; align-items: center; gap: var(--space-2); min-width: 0;
    height: var(--space-5); background: transparent;
    border: var(--border-hairline) solid transparent; border-radius: var(--radius-pill);
    color: var(--text-muted); cursor: pointer; font-family: var(--font-mono);
    font-size: var(--type-xs); line-height: 1.2; padding: 0 var(--space-2);
  }
  .delivery-mode-trigger:hover { border-color: var(--border-color); color: var(--text-strong); }
  .delivery-mode-trigger.is-open {
    background: var(--surface-2); border-color: var(--border-color); color: var(--text-strong);
  }
  .delivery-mode-trigger:disabled { cursor: default; opacity: .5; }
  .delivery-mode-face { white-space: nowrap; }
  .delivery-mode-chevron { color: var(--text-faintest); flex: none; }
  .delivery-mode-panel {
    position: absolute; z-index: 40; right: 0; bottom: calc(100% + var(--space-1));
    width: 148px; padding-block: var(--space-1); background: var(--surface-2);
    border: var(--border-hairline) solid var(--border-color); border-radius: var(--radius-lg);
    overflow: hidden; outline: none;
  }
  .delivery-mode-choice {
    display: flex; align-items: center; justify-content: space-between; gap: var(--space-3);
    width: 100%; background: transparent; border: 0; color: var(--text-default);
    cursor: pointer; font: inherit; font-size: var(--type-sm); padding: var(--space-2) var(--space-3);
    text-align: left;
  }
  .delivery-mode-choice:hover { background-image: var(--interaction-hover); color: var(--text-strong); }
  .delivery-mode-choice.cursor { background: var(--surface-recessed); color: var(--text-strong); }
  .delivery-mode-choice.on { color: var(--text-strong); }
  .delivery-mode-tick { flex: none; font-family: var(--font-mono); font-size: var(--type-xs); }
</style>
