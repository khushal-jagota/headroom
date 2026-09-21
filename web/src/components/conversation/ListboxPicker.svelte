<script lang="ts">
  import { tick, type Snippet } from "svelte";

  export type ListboxPickerItem = {
    value: string;
    name: string;
  };

  export type ListboxPickerController = {
    close: (returnFocus?: boolean) => void;
    focusList: () => void;
    setActiveValue: (value: string | null) => void;
  };

  let {
    items,
    selectedValue,
    disabled = false,
    selectionDisabled = disabled,
    keepOpenWhenDisabled = false,
    label,
    listLabel = label,
    kind = "compact",
    below = false,
    align = "left",
    attributes = {},
    triggerAttributes = {},
    panelAttributes = {},
    optionAttributes,
    panelBusy = false,
    controller = $bindable(),
    onOpen,
    onChoose,
    triggerContent,
    beforeList,
    optionContent,
    afterList
  }: {
    items: readonly ListboxPickerItem[];
    selectedValue: string | null;
    disabled?: boolean;
    selectionDisabled?: boolean;
    keepOpenWhenDisabled?: boolean;
    label: string;
    listLabel?: string;
    kind?: "model" | "compact";
    below?: boolean;
    align?: "left" | "right";
    attributes?: Record<string, string | undefined>;
    triggerAttributes?: Record<string, string | undefined>;
    panelAttributes?: Record<string, string | undefined>;
    optionAttributes?: (
      item: ListboxPickerItem,
      active: boolean,
      selected: boolean
    ) => Record<string, string | undefined>;
    panelBusy?: boolean;
    controller?: ListboxPickerController;
    onOpen?: () => void;
    onChoose: (value: string) => void;
    triggerContent: Snippet<[boolean]>;
    beforeList?: Snippet;
    optionContent: Snippet<[ListboxPickerItem, boolean]>;
    afterList?: Snippet;
  } = $props();

  const listId = $props.id();
  let open = $state(false);
  let activeIndex = $state(0);
  let root = $state<HTMLDivElement | null>(null);
  let trigger = $state<HTMLButtonElement | null>(null);
  let list = $state<HTMLDivElement | null>(null);
  let typeahead = "";
  let typeaheadTimer: ReturnType<typeof setTimeout> | undefined;
  let returnFocusWhenEnabled = false;
  // Hover bookkeeping. Plain locals: this must never re-render the list.
  let pointerSpot: { x: number; y: number } | null = null;
  let pointerReallyMoved = false;

  let active = $derived(items.length === 0 ? 0 : Math.min(activeIndex, items.length - 1));

  function focusList(): void {
    void tick().then(() => list?.focus());
  }

  function setActiveValue(value: string | null): void {
    const index = value === null ? -1 : items.findIndex((item) => item.value === value);
    activeIndex = Math.max(0, index);
    focusList();
  }

  function close(returnFocus = true): void {
    if (returnFocus) trigger?.focus();
    open = false;
    typeahead = "";
    if (typeaheadTimer !== undefined) clearTimeout(typeaheadTimer);
  }

  controller = { close, focusList, setActiveValue };

  // Hold focus on the picker until its disabled trigger can receive it again.
  $effect.pre(() => {
    if (disabled && open && !keepOpenWhenDisabled) {
      open = false;
      returnFocusWhenEnabled = true;
      root?.focus();
    }
  });

  $effect(() => {
    if (disabled || !returnFocusWhenEnabled) return;
    const focusRemainsInside = root?.contains(document.activeElement) ?? false;
    returnFocusWhenEnabled = false;
    if (focusRemainsInside) trigger?.focus();
  });

  /* A panel that mounts or reflows under a still pointer receives a mouse move at the
   * pointer's resting place. The browser is reporting geometry there, not a person
   * choosing a row, and acting on it takes the active option away from the keyboard.
   * This capture listener runs on the same event, just before the option's own handler,
   * so the handler can tell the two apart by whether the pointer changed place. */
  $effect(() => {
    function trackPointer(event: MouseEvent): void {
      pointerReallyMoved =
        pointerSpot !== null && (event.clientX !== pointerSpot.x || event.clientY !== pointerSpot.y);
      pointerSpot = { x: event.clientX, y: event.clientY };
    }
    document.addEventListener("mousemove", trackPointer, { capture: true, passive: true });
    return () => document.removeEventListener("mousemove", trackPointer, true);
  });

  $effect(() => {
    if (!open) return;
    function closeOutside(event: PointerEvent): void {
      if (event.target instanceof Node && root !== null && !root.contains(event.target)) close(false);
    }
    document.addEventListener("pointerdown", closeOutside, true);
    return () => document.removeEventListener("pointerdown", closeOutside, true);
  });

  $effect(() => {
    active;
    list?.querySelector<HTMLElement>("[data-listbox-picker-active]")?.scrollIntoView({ block: "nearest" });
  });

  async function openPanel(): Promise<void> {
    if (disabled) return;
    onOpen?.();
    await tick();
    activeIndex = Math.max(0, items.findIndex((item) => item.value === selectedValue));
    open = true;
    await tick();
    list?.focus();
  }

  function choose(value: string): void {
    if (disabled || selectionDisabled) return;
    onChoose(value);
  }

  function onTriggerKeydown(event: KeyboardEvent): void {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    void openPanel();
  }

  function actionButtons(): HTMLButtonElement[] {
    return root === null
      ? []
      : [...root.querySelectorAll<HTMLButtonElement>("[data-listbox-picker-action]:not(:disabled)")];
  }

  function focusAction(index: number): void {
    const actions = actionButtons();
    if (actions.length === 0) return;
    actions[(index + actions.length) % actions.length]?.focus();
  }

  function moveToMatch(key: string): void {
    typeahead += key.toLocaleLowerCase();
    if (typeaheadTimer !== undefined) clearTimeout(typeaheadTimer);
    typeaheadTimer = setTimeout(() => (typeahead = ""), 500);
    const start = items.length === 0 ? 0 : (active + 1) % items.length;
    for (let offset = 0; offset < items.length; offset += 1) {
      const index = (start + offset) % items.length;
      if (items[index]?.name.toLocaleLowerCase().startsWith(typeahead)) {
        activeIndex = index;
        return;
      }
    }
  }

  function onListKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (items.length === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : items.length - 1;
      activeIndex = (active + step) % items.length;
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      const actions = actionButtons();
      const selectedAction = actions.findIndex((action) => action.getAttribute("aria-pressed") === "true");
      focusAction(selectedAction < 0 ? 0 : selectedAction);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      activeIndex = event.key === "Home" ? 0 : items.length - 1;
      return;
    }
    if ((event.key === "Enter" || event.key === " ") && items[active] !== undefined) {
      event.preventDefault();
      choose(items[active].value);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      moveToMatch(event.key);
    }
  }

  function onPanelKeydown(event: KeyboardEvent): void {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement) || !target.hasAttribute("data-listbox-picker-action")) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      list?.focus();
      return;
    }
    const actions = actionButtons();
    const index = actions.indexOf(target);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      focusAction(index + (event.key === "ArrowDown" ? 1 : -1));
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      focusAction(event.key === "Home" ? 0 : actions.length - 1);
    }
  }
</script>

<div class="listbox-picker" class:model={kind === "model"} bind:this={root} tabindex="-1" {...attributes}>
  <button
    type="button"
    class="listbox-picker-trigger"
    class:is-open={open}
    bind:this={trigger}
    aria-haspopup="listbox"
    aria-expanded={open}
    aria-controls={open ? listId : undefined}
    aria-label={label}
    {disabled}
    onkeydown={onTriggerKeydown}
    onclick={() => (open ? close() : void openPanel())}
    {...triggerAttributes}
  >
    {@render triggerContent(open)}
    <span class="listbox-picker-chevron" aria-hidden="true">{open ? "⌃" : "⌄"}</span>
  </button>

  {#if open}
    <div
      class="listbox-picker-panel"
      class:model={kind === "model"}
      class:compact={kind === "compact"}
      class:below
      class:right={align === "right"}
      aria-busy={panelBusy}
      role="presentation"
      onkeydown={onPanelKeydown}
      onfocusout={(event) => {
        if (!(event.relatedTarget instanceof Node) || !root?.contains(event.relatedTarget)) close(false);
      }}
      {...panelAttributes}
    >
      {#if beforeList}{@render beforeList()}{/if}
      <div
        class="listbox-picker-list"
        bind:this={list}
        id={listId}
        role="listbox"
        aria-label={listLabel}
        aria-activedescendant={items.length === 0 ? undefined : `${listId}-${active}`}
        tabindex="-1"
        onkeydown={onListKeydown}
      >
        {#each items as item, index (item.value)}
          <button
            type="button"
            class="listbox-picker-option"
            class:cursor={index === active}
            class:on={item.value === selectedValue}
            id={`${listId}-${index}`}
            role="option"
            tabindex="-1"
            aria-selected={item.value === selectedValue}
            disabled={selectionDisabled}
            data-listbox-picker-value={item.value}
            data-listbox-picker-active={index === active ? "true" : undefined}
            onmousemove={() => { if (pointerReallyMoved) activeIndex = index; }}
            onmousedown={(event) => event.preventDefault()}
            onclick={() => choose(item.value)}
            {...optionAttributes?.(item, index === active, item.value === selectedValue)}
          >
            {@render optionContent(item, item.value === selectedValue)}
            <span class="listbox-picker-tick" aria-hidden="true">{item.value === selectedValue ? "✓" : ""}</span>
          </button>
        {/each}
      </div>
      {#if afterList}{@render afterList()}{/if}
    </div>
  {/if}
</div>

<style>
  .listbox-picker { position: relative; display: inline-flex; min-width: 0; }
  .listbox-picker-trigger {
    display: inline-flex; align-items: center; gap: var(--space-2); min-width: 0; max-width: 100%;
    background: transparent; border: var(--border-hairline) solid transparent;
    border-radius: var(--radius-pill); color: var(--text-muted); cursor: pointer;
    font-family: var(--font-mono); font-size: var(--type-xs); line-height: 1.2;
    padding: var(--space-1) var(--space-2);
  }
  .listbox-picker-trigger:hover { border-color: var(--border-color); color: var(--text-strong); }
  .listbox-picker-trigger.is-open { background: var(--surface-2); border-color: var(--border-color); color: var(--text-strong); }
  .listbox-picker-trigger:disabled { cursor: default; opacity: .5; }
  .listbox-picker-chevron { color: var(--text-faintest); flex: none; }
  .listbox-picker-panel {
    position: absolute; z-index: 40; bottom: calc(100% + var(--space-1)); left: 0;
    background: var(--surface-2); border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-lg); overflow: hidden;
  }
  .listbox-picker-panel.model {
    display: grid; grid-template-columns: auto minmax(0, 1fr); width: 296px;
    max-width: calc(100vw - var(--space-4) * 2);
  }
  .listbox-picker-panel.compact {
    width: min(148px, calc(100vw - var(--space-4) * 2));
    max-width: calc(100vw - var(--space-4) * 2); padding-block: var(--space-1);
  }
  .listbox-picker-panel.below { top: calc(100% + var(--space-1)); bottom: auto; }
  .listbox-picker-panel.right { right: 0; left: auto; }
  .listbox-picker-list { min-width: 0; padding-block: var(--space-1); outline: none; }
  .compact > .listbox-picker-list { padding-block: 0; }
  .listbox-picker-option {
    display: flex; align-items: center; justify-content: space-between; gap: var(--space-3);
    width: 100%; background: transparent; border: 0; color: var(--text-default);
    cursor: pointer; font: inherit; font-size: var(--type-sm); padding: var(--space-2) var(--space-3);
    text-align: left;
  }
  .listbox-picker-option:hover { background-image: var(--interaction-hover); color: var(--text-strong); }
  .listbox-picker-option.cursor { background: var(--surface-recessed); color: var(--text-strong); }
  .listbox-picker-option.on { color: var(--text-strong); }
  .listbox-picker-tick { flex: none; font-family: var(--font-mono); font-size: var(--type-xs); }
  .listbox-picker-panel[aria-busy="true"] .listbox-picker-option { cursor: progress; opacity: .55; }
  @media (max-width: 620px) {
    .listbox-picker-panel.model { width: min(274px, calc(100vw - var(--space-4) * 2)); }
  }
</style>
