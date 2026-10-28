<script lang="ts">
  import { tick, type Snippet } from "svelte";

  export type ListboxPickerItem = {
    value: string;
    name: string;
  };

  export type ListboxPageWindow = {
    offset: number; total: number; rowHeight: number; activeIndex?: number;
    onWindow: (index: number) => void;
    onTypeahead: (prefix: string, after: number) => void;
  };

  export type ListboxPickerController = {
    open: () => void;
    readonly expanded: boolean;
    readonly controls: string | undefined;
    close: (returnFocus?: boolean) => void;
    focusList: () => void;
    setActiveValue: (value: string | null) => void;
  };

  let {
    items,
    pageWindow,
    selectedValue,
    disabled = false,
    selectionDisabled = disabled,
    keepOpenWhenDisabled = false,
    label,
    listLabel = label,
    kind = "compact",
    chevron = "typed",
    scrollPanel = false,
    hideTrigger = false,
    below = false,
    align = "left",
    anchor = null,
    attributes = {},
    triggerAttributes = {},
    panelAttributes = {},
    optionAttributes,
    panelBusy = false,
    controller = $bindable(),
    onOpen,
    onClose,
    onChoose,
    onActiveChange,
    triggerContent,
    beforeList,
    optionContent,
    afterList
  }: {
    items: readonly ListboxPickerItem[];
    pageWindow?: ListboxPageWindow;
    selectedValue: string | null;
    disabled?: boolean;
    selectionDisabled?: boolean;
    keepOpenWhenDisabled?: boolean;
    label: string;
    listLabel?: string;
    kind?: "rail" | "compact";
    /** How the trigger's chevron is drawn. A typed caret is sized by the font rather
     *  than by its box, so it reads small beside stroked icons. The conversation's
     *  composer asks for the drawn one; every other caller keeps what it had. */
    chevron?: "typed" | "drawn" | "none";
    scrollPanel?: boolean;
    hideTrigger?: boolean;
    below?: boolean;
    align?: "left" | "right";
    /** Optional second entry point for the same picker; keeps one panel and draft. */
    anchor?: HTMLElement | null;
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
    /** Every close, dismissal or not. A picker with a draft discards it here. */
    onClose?: () => void;
    onChoose: (value: string) => void;
    /** Optional explanatory preview only; activation still belongs to onChoose. */
    onActiveChange?: (value: string | null) => void;
    triggerContent: Snippet<[boolean]>;
    beforeList?: Snippet;
    optionContent: Snippet<[ListboxPickerItem, boolean]>;
    afterList?: Snippet;
  } = $props();

  const listId = $props.id();
  let open = $state(false);
  let anchorOffset = $state<{left:number;top:number} | null>(null);
  let anchorPositionReady = $state(true);
  let activeIndex = $state(0);
  let root = $state<HTMLDivElement | null>(null);
  let trigger = $state<HTMLButtonElement | null>(null);
  let list = $state<HTMLDivElement | null>(null);
  let panelElement = $state<HTMLDivElement | null>(null);
  let panelBounds = $state<{left: number; top: number; height: number} | null>(null);
  let typeahead = "";
  let typeaheadTimer: ReturnType<typeof setTimeout> | undefined;
  let returnFocusWhenEnabled = false;
  // Hover bookkeeping. Plain locals: this must never re-render the list.
  let pointerSpot: { x: number; y: number } | null = null;
  let pointerReallyMoved = false;

  let itemOffset = $derived(pageWindow?.offset ?? 0);
  let totalItems = $derived(pageWindow?.total ?? items.length);
  let active = $derived(totalItems === 0 ? 0 : Math.min(activeIndex, totalItems - 1));
  let localActive = $derived(active-itemOffset);
  $effect(() => { const index=pageWindow?.activeIndex; if(index!==undefined) activeIndex=index; });
  function activate(index:number):void {
    activeIndex=index;
    if(pageWindow && (index<itemOffset || index>=itemOffset+items.length)) pageWindow.onWindow(index);
  }
  function scrollWindow():void {
    if(!pageWindow || !list) return;
    const index=Math.max(0,Math.floor(list.scrollTop/pageWindow.rowHeight));
    if(index<itemOffset || (index+8>=itemOffset+items.length && itemOffset+items.length<totalItems)) pageWindow.onWindow(index);
  }

  $effect(() => {
    onActiveChange?.(open ? items[localActive]?.value ?? null : null);
  });

  function focusList(): void {
    void tick().then(() => list?.focus(anchor ? {preventScroll:true} : undefined));
  }

  function setActiveValue(value: string | null): void {
    const index = value === null ? -1 : items.findIndex((item) => item.value === value);
    activate(itemOffset+Math.max(0, index));
    // A rail switch puts different words in the list. Letters typed at the old one must
    // not join letters typed at the new one, or the match silently finds nothing.
    forgetTypeahead();
    focusList();
  }

  function forgetTypeahead(): void {
    typeahead = "";
    if (typeaheadTimer !== undefined) clearTimeout(typeaheadTimer);
  }

  function close(returnFocus = true): void {
    if (returnFocus) (anchor ?? trigger)?.focus();
    if (open) onClose?.();
    open = false;
    forgetTypeahead();
  }

  controller = { open: () => void openPanel(), close, focusList, setActiveValue, get expanded() { return open; }, get controls() { return open ? listId : undefined; } };

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
    if (anchor && !anchorPositionReady) return;
    active; itemOffset; items.length;
    list?.querySelector<HTMLElement>("[data-listbox-picker-active]")?.scrollIntoView({ block: "nearest" });
  });

  async function openPanel(): Promise<void> {
    if (disabled) return;
    onOpen?.();
    await tick();
    const rootRect = root?.getBoundingClientRect();
    const anchorRect = anchor?.getBoundingClientRect();
    anchorOffset = rootRect && anchorRect ? {left:anchorRect.left-rootRect.left,top:anchorRect.bottom-rootRect.top} : null;
    anchorPositionReady = anchorOffset === null;
    activate(itemOffset+Math.max(0, items.findIndex((item) => item.value === selectedValue)));
    open = true;
    panelBounds = null;
    await tick();
    if (scrollPanel && panelElement && trigger) {
      const triggerRect = trigger.getBoundingClientRect();
      const panelRect = panelElement.getBoundingClientRect();
      const dock = trigger.closest("[data-conversation-pane]")?.getBoundingClientRect();
      const bottom = Math.min(window.innerHeight, dock?.bottom ?? window.innerHeight) - 8;
      const top = below ? triggerRect.bottom + 4 : Math.max(8, triggerRect.top - panelRect.height - 4);
      panelBounds = {left: Math.max(8, Math.min(panelRect.left, window.innerWidth - panelRect.width - 8)), top, height: Math.max(48, (below ? bottom : triggerRect.top - 4) - top)};
      await tick();
    }
    // An alternate entry point can sit near the viewport edge. Bound the same
    // panel using its rendered width; its draft, keyboard flow and focus stay here.
    if (anchorOffset && panelElement && root) {
      const panelRect = panelElement.getBoundingClientRect();
      const rootRect = root.getBoundingClientRect();
      const left = Math.max(0,Math.min(panelRect.left,document.documentElement.clientWidth-panelRect.width));
      anchorOffset = { ...anchorOffset, left:left-rootRect.left };
      // Publish the bounded position before focus or the active-row scroll effect.
      await tick();
    }
    anchorPositionReady = true;
    list?.focus(anchor ? {preventScroll:true} : undefined);
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
    if(pageWindow) { pageWindow.onTypeahead(typeahead,active); return; }
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
    if (totalItems === 0) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : totalItems - 1;
      activate((active + step) % totalItems);
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
      activate(event.key === "Home" ? 0 : totalItems - 1);
      return;
    }
    if (event.key === " " && pageWindow && typeahead) {
      event.preventDefault(); moveToMatch(" "); return;
    }
    if ((event.key === "Enter" || event.key === " ") && items[localActive] !== undefined) {
      event.preventDefault();
      choose(items[localActive].value);
      return;
    }
    if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      moveToMatch(event.key);
    }
  }

  function onPanelKeydown(event: KeyboardEvent): void {
    const target = event.target;
    if (!(target instanceof HTMLButtonElement) || !target.hasAttribute("data-listbox-picker-action")) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      // A rail action can remove its own button (Models does). Keep focus inside
      // the surviving list before that removal, so focusout cannot close the picker.
      list?.focus();
      target.click();
      return;
    }
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

<div class="listbox-picker" class:scroll-panel={scrollPanel} bind:this={root} tabindex="-1" {...attributes}>
  {#if !hideTrigger}
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
    {#if chevron === "drawn"}
      <svg class="listbox-picker-chevron-drawn" viewBox="0 0 24 24" aria-hidden="true">
        {#if open}<path d="M6 15l6-6 6 6" />{:else}<path d="M6 9l6 6 6-6" />{/if}
      </svg>
    {:else if chevron === "typed"}
      <span class="listbox-picker-chevron" aria-hidden="true">{open ? "⌃" : "⌄"}</span>
    {/if}
  </button>
  {/if}

  {#if open}
    <div
      class="listbox-picker-panel"
      bind:this={panelElement}
      class:rail={kind === "rail"}
      class:compact={kind === "compact"}
      class:below
      class:right={align === "right"}
      class:scroll-panel={scrollPanel}
      style:position={panelBounds ? "fixed" : undefined}
      style:max-height={panelBounds ? `${panelBounds.height}px` : undefined}
      style:left={panelBounds ? `${panelBounds.left}px` : anchorOffset ? `${anchorOffset.left}px` : undefined}
      style:top={panelBounds ? `${panelBounds.top}px` : anchorOffset ? `calc(${anchorOffset.top}px + var(--space-1))` : undefined}
      style:bottom={panelBounds || anchorOffset ? "auto" : undefined}
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
        aria-activedescendant={items[localActive] === undefined ? undefined : `${listId}-${active}`}
        tabindex="-1"
        onkeydown={onListKeydown}
        onscroll={scrollWindow}
        style:max-height={pageWindow ? '288px' : undefined}
        style:overflow-y={pageWindow ? 'auto' : undefined}
      >
        {#if pageWindow}<div aria-hidden="true" style:height={`${itemOffset*pageWindow.rowHeight}px`}></div>{/if}
        {#each items as item, index (item.value)}
          <button
            type="button"
            class="listbox-picker-option"
            class:cursor={index+itemOffset === active}
            class:on={item.value === selectedValue}
            id={`${listId}-${index+itemOffset}`}
            role="option"
            aria-posinset={pageWindow ? index+itemOffset+1 : undefined}
            aria-setsize={pageWindow ? totalItems : undefined}
            style:height={pageWindow ? `${pageWindow.rowHeight}px` : undefined}
            tabindex="-1"
            aria-selected={item.value === selectedValue}
            disabled={selectionDisabled}
            data-listbox-picker-value={item.value}
            data-listbox-picker-active={index+itemOffset === active ? "true" : undefined}
            onmousemove={() => { if (pointerReallyMoved) activeIndex = index+itemOffset; }}
            onmousedown={(event) => event.preventDefault()}
            onclick={() => choose(item.value)}
            {...optionAttributes?.(item, index+itemOffset === active, item.value === selectedValue)}
          >
            {@render optionContent(item, item.value === selectedValue)}
            <span class="listbox-picker-tick" aria-hidden="true">{item.value === selectedValue ? "✓" : ""}</span>
          </button>
        {/each}
        {#if pageWindow}<div aria-hidden="true" style:height={`${Math.max(0,totalItems-itemOffset-items.length)*pageWindow.rowHeight}px`}></div>{/if}
      </div>
      {#if afterList}{@render afterList()}{/if}
    </div>
  {/if}
</div>

<style>
  .listbox-picker { position: relative; display: inline-flex; min-width: 0; }
  .listbox-picker.scroll-panel { max-width: 100%; }
  .listbox-picker.scroll-panel .listbox-picker-trigger { min-height: 24px; }
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
  .listbox-picker-chevron-drawn {
    flex: none;
    width: 16px;
    height: 16px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.7;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
  .listbox-picker-panel {
    position: absolute; z-index: 40; bottom: calc(100% + var(--space-1)); left: 0;
    background: var(--surface-2); border: var(--border-hairline) solid var(--border-color);
    border-radius: var(--radius-lg); overflow: hidden;
  }
  .listbox-picker-panel.rail {
    display: grid; grid-template-columns: auto minmax(0, 1fr); width: 296px;
    max-width: calc(100vw - var(--space-4) * 2);
  }
  .listbox-picker-panel.compact {
    width: min(148px, calc(100vw - var(--space-4) * 2));
    max-width: calc(100vw - var(--space-4) * 2); padding-block: var(--space-1);
  }
  .listbox-picker-panel.below { top: calc(100% + var(--space-1)); bottom: auto; }
  .listbox-picker-panel.right { right: 0; left: auto; }
  .listbox-picker-panel.scroll-panel { overflow-y: auto; overscroll-behavior: contain; }
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
    .listbox-picker-panel.rail { width: min(274px, calc(100vw - var(--space-4) * 2)); }
  }
</style>
